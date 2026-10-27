import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { uid } from './store.mjs';

export class Voice {
  constructor(store) { this.store = store; this.pending = new Map(); this.phase = 'idle'; }
  launch() {
    if (this.child) return;
    this.buffer = ''; this.stderr = '';
    const child = spawn(this.store.config.voice.python || 'python', [process.env.LH_VOICE_WORKER_PATH || fileURLToPath(new URL('../python/voice_worker.py', import.meta.url))], {
      windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', ...(process.env.LH_SPEECH_ROOT ? { LH_SPEECH_ROOT: process.env.LH_SPEECH_ROOT } : {}) },
    });
    this.child = child;
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => {
      this.buffer += chunk.toString('utf8');
      let i;
      while ((i = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, i); this.buffer = this.buffer.slice(i + 1);
        let message;
        try { message = JSON.parse(line); } catch { continue; }
        const pending = this.pending.get(message.id);
        if (pending) { clearTimeout(pending.timer); this.pending.delete(message.id); message.error ? pending.reject(new Error(message.error)) : pending.resolve(message.result); }
      }
    });
    child.stderr.on('data', data => { this.stderr = (this.stderr + data.toString('utf8')).slice(-2000); });
    const failed = message => {
      if (this.child !== child) return;
      this.child = null;
      this.phase = 'idle';
      for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(new Error(message)); }
      this.pending.clear();
    };
    child.on('error', error => failed(`无法启动 Python：${error.message}，请检查语音设置`));
    child.on('exit', code => failed(`语音进程退出（${code}）${this.stderr ? '：' + this.stderr : ''}`));
  }
  request(command) {
    this.launch(); const id = uid();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.cancel(); reject(new Error('语音处理超时，请检查 Python、依赖和模型目录')); }, ['stop', 'warmup'].includes(command) ? 240000 : 15000);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ id, command, config: this.store.config.voice }) + '\n');
    });
  }
  async start() { this.phase = 'recording'; try { return await this.request('start'); } catch (error) { this.phase = 'idle'; throw error; } }
  async stop() { this.phase = 'transcribing'; try { return await this.request('stop'); } finally { this.phase = 'idle'; } }
  cancel() {
    const child = this.child; this.child = null; this.phase = 'idle';
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(new Error('语音处理已停止')); }
    this.pending.clear();
    if (child) { child.stdin.end(); child.kill(); }
  }
}
