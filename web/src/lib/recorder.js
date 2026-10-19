import workletUrl from './pcm-worklet.js?url';
import { encodeWav } from './wav.js';

export const MAX_RECORD_SECONDS = 90;

export class VoiceRecorder {
  constructor(onLevel = () => {}, onInterrupted = () => {}) {
    this.onLevel = onLevel;
    this.onInterrupted = onInterrupted;
    this.chunks = [];
    this.cancelled = false;
  }
  async start() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('当前浏览器无法使用麦克风，请通过 localhost 或 HTTPS 打开页面。');
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true,
      } });
      if (this.cancelled) { this.stream.getTracks().forEach(track => track.stop()); return; }
      this.stream.getAudioTracks().forEach(track => track.addEventListener('ended', this.onInterrupted));
      this.context = new AudioContext();
      await this.context.resume();
      await this.context.audioWorklet.addModule(workletUrl);
      if (this.cancelled) { await this.cancel(); return; }
      this.node = new AudioWorkletNode(this.context, 'pcm-capture', { channelCount: 1, channelCountMode: 'explicit' });
      this.node.port.onmessage = ({ data }) => {
        if (data.samples) {
          this.chunks.push(data.samples);
          const rms = Math.sqrt(data.samples.reduce((sum, n) => sum + n * n, 0) / data.samples.length);
          this.onLevel(Math.min(1, rms * 7));
        }
        if (data.stopped) this.resolveStop?.();
      };
      this.node.onprocessorerror = this.onInterrupted;
      this.source = this.context.createMediaStreamSource(this.stream);
      this.source.connect(this.node);
      // Worklet 输出为零：保持音频图运行，无麦克风回放。
      this.node.connect(this.context.destination);
    } catch (error) {
      await this.cancel();
      if (error.name === 'NotAllowedError') throw new Error('麦克风权限未开启，请在浏览器地址栏允许访问后重试。');
      if (error.name === 'NotFoundError') throw new Error('未找到麦克风，请连接输入设备后重试。');
      if (error.name === 'NotReadableError') throw new Error('麦克风暂时不可用，请检查设备是否被其他应用占用。');
      throw error;
    }
  }
  async stop() {
    if (!this.node) throw new Error('录音尚未准备好，请重试。');
    const sampleRate = this.context.sampleRate;
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('录音设备未响应，请重新录音。')), 2000);
        this.resolveStop = () => { clearTimeout(timer); resolve(); };
        this.node.port.postMessage('stop');
      });
      const length = this.chunks.reduce((n, chunk) => n + chunk.length, 0);
      if (length < sampleRate * 0.25) throw new Error('录音太短，请说完口令后再结束。');
      let samples = new Float32Array(length);
      let offset = 0;
      this.chunks.forEach(chunk => { samples.set(chunk, offset); offset += chunk.length; });
      if (sampleRate !== 16000) {
        const offline = new OfflineAudioContext(1, Math.ceil(length * 16000 / sampleRate), 16000);
        const buffer = offline.createBuffer(1, length, sampleRate);
        buffer.copyToChannel(samples, 0);
        const source = offline.createBufferSource();
        source.buffer = buffer; source.connect(offline.destination); source.start();
        samples = (await offline.startRendering()).getChannelData(0);
      }
      return new Blob([encodeWav(samples)], { type: 'audio/wav' });
    } finally {
      await this.cancel();
    }
  }
  async cancel() {
    this.cancelled = true;
    this.stream?.getTracks().forEach(track => {
      track.removeEventListener('ended', this.onInterrupted);
      track.stop();
    });
    this.source?.disconnect();
    this.node?.disconnect();
    if (this.node) this.node.port.close();
    if (this.context && this.context.state !== 'closed') await this.context.close();
    this.chunks = [];
  }
}
