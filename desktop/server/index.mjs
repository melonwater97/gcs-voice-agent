import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Store, mergeMasked, validateSettings, uid } from './store.mjs';
import { Knowledge, readSkill } from './knowledge.mjs';
import { Engine } from './engine.mjs';
import { Voice } from './voice.mjs';
import { connectMcp } from './mcp.mjs';
import { validateWorkflow, starterWorkflow } from './workflows.mjs';
import { prepareRuntimeConfig } from './runtime-config.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
export async function startServer({ port = Number(process.env.DESKTOP_PORT || 18301), dataDir = process.env.LH_DATA_DIR || path.join(root, '.data'), token = process.env.LH_BACKEND_TOKEN || '', encryptionKey = process.env.LH_CREDENTIAL_KEY } = {}) {
  const store = new Store(dataDir, encryptionKey), knowledge = new Knowledge(store), clients = new Set();
  prepareRuntimeConfig(store);
  const notify = event => { const line = `data: ${JSON.stringify(store.safe(event))}\n\n`; for (const client of clients) client.write(line); };
  const engine = new Engine(store, knowledge, notify), voice = new Voice(store);
  const idle = () => { engine.assertIdle(); if (voice.phase !== 'idle') throw Object.assign(new Error('请先结束语音输入'), { statusCode: 409 }); };
  async function body(req) {
    if (!req.headers['content-type']?.startsWith('application/json')) throw new Error('请求需要 application/json');
    let count = 0, buffers = [];
    for await (const chunk of req) { count += chunk.length; if (count > 29 * 1024 * 1024) throw new Error('请求体超过大小上限'); buffers.push(chunk); }
    return JSON.parse(Buffer.concat(buffers).toString('utf8') || '{}');
  }
  const server = http.createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    try {
      const host = req.headers.host?.split(':')[0];
      if (!['127.0.0.1', 'localhost'].includes(host)) throw Object.assign(new Error('不支持的主机'), { statusCode: 403 });
      const url = new URL(req.url, 'http://127.0.0.1'), route = url.pathname;
      if (route.startsWith('/api/')) {
        const origin = req.headers.origin;
        if (origin && ![`http://127.0.0.1:${port}`, 'http://127.0.0.1:5180', 'http://127.0.0.1:5181'].includes(origin)) throw Object.assign(new Error('不支持的请求来源'), { statusCode: 403 });
        if (token && req.headers.authorization !== `Bearer ${token}`) throw Object.assign(new Error('未授权'), { statusCode: 401 });
      }
      let result;
      if (route === '/api/events' && req.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' }); res.write(': connected\n\n');
        clients.add(res); const heartbeat = setInterval(() => res.write(': heartbeat\n\n'), 15000);
        req.on('close', () => { clearInterval(heartbeat); clients.delete(res); }); return;
      }
      if (route === '/api/state' && req.method === 'GET') result = { config: store.publicConfig(), sessions: store.listSessions(), knowledge: knowledge.list(), active: engine.active ? { sessionId: engine.active.sessionId, runId: engine.active.runId } : null, voice: voice.phase, version: '0.1.0', engine: 'Pi 1.4.0' };
      else if (route === '/api/settings' && req.method === 'PUT') {
        idle(); const config = await body(req); for (const flow of config.workflows || []) validateWorkflow(flow);
        result = store.saveConfig(config); voice.cancel(); notify({ type: 'settings' });
      } else if (route === '/api/sessions' && req.method === 'POST') { idle(); result = store.newSession(); }
      else if (/^\/api\/sessions\/[\w-]+$/.test(route) && req.method === 'GET') result = store.safe(store.getSession(route.split('/').at(-1)));
      else if (route === '/api/run' && req.method === 'POST') { if (voice.phase !== 'idle') throw new Error('请先结束语音输入'); const value = await body(req); result = engine.start(value.sessionId, value.text, { workflowId: value.workflowId, input: value.input }); }
      else if (route === '/api/stop' && req.method === 'POST') { voice.cancel(); await engine.stop(); result = { stopped: true }; }
      else if (route === '/api/knowledge' && req.method === 'POST') { idle(); const value = await body(req); result = await knowledge.add(value.name, value.base64); }
      else if (/^\/api\/knowledge\/[\w-]+$/.test(route) && req.method === 'PATCH') { idle(); const value = await body(req); knowledge.toggle(route.split('/').at(-1), value.enabled); result = knowledge.list(); }
      else if (/^\/api\/knowledge\/[\w-]+$/.test(route) && req.method === 'DELETE') { idle(); knowledge.remove(route.split('/').at(-1)); result = knowledge.list(); }
      else if (route === '/api/knowledge/search' && req.method === 'POST') result = knowledge.search((await body(req)).query || '');
      else if (route === '/api/skills/import' && req.method === 'POST') {
        idle(); const value = await body(req), skill = readSkill(value.directory);
        if (store.config.skills.some(s => s.name === skill.name)) throw new Error('同名 Skill 已存在');
        const { content: _, file: __, ...metadata } = skill;
        store.saveConfig({ ...store.config, skills: [...store.config.skills, { ...metadata, id: uid(), enabled: true }] }); result = store.publicConfig().skills;
      } else if (route === '/api/skills/preview' && req.method === 'POST') { const value = await body(req), skill = store.config.skills.find(s => s.id === value.id); if (!skill) throw new Error('Skill 不存在'); result = readSkill(skill.directory); }
      else if (route === '/api/workflows/starter' && req.method === 'POST') { idle(); const flow = { ...starterWorkflow, id: uid() }; store.saveConfig({ ...store.config, workflows: [...store.config.workflows, flow] }); result = store.publicConfig().workflows; }
      else if (route === '/api/diagnostics' && req.method === 'POST') {
        const value = await body(req);
        if (value.type === 'voice') result = await voice.request('probe');
        else if (value.type === 'asr') result = await voice.request('warmup');
        else if (value.type === 'mcp') {
          const draft = mergeMasked(value.config || store.config.mcp.find(s => s.id === value.id), store.config.mcp.find(s => s.id === value.id));
          if (!draft) throw new Error('MCP 配置不存在');
          validateSettings({ ...store.config, mcp: [draft] }); const connection = await connectMcp(draft);
          try { result = { connected: true, tools: connection.tools }; } finally { await connection.close(); }
        } else {
          const c = store.config;
          const response = await fetch(value.type === 'model' ? c.model.baseUrl.replace(/\/chat\/completions\/?$/, '').replace(/\/$/, '') + '/models' : c.gcs.baseUrl, { headers: value.type === 'model' && c.model.apiKey ? { Authorization: `Bearer ${c.model.apiKey}` } : {}, signal: AbortSignal.timeout(8000) });
          if (value.type === 'model' && !response.ok) throw new Error(`模型服务返回 HTTP ${response.status}`);
          result = { reachable: true, status: response.status, message: value.type === 'model' ? '模型服务可访问；工具调用需通过实际对话验证' : 'HTTP 服务可访问；业务契约需通过实际请求验证' };
        }
      } else if (route === '/api/voice/start' && req.method === 'POST') { idle(); result = await voice.start(); }
      else if (route === '/api/voice/devices' && req.method === 'GET') result = await voice.request('probe');
      else if (route === '/api/voice/status' && req.method === 'GET') result = { ...(await voice.request('status')), phase: voice.phase };
      else if (route === '/api/voice/stop' && req.method === 'POST') {
        const value = await body(req), session = store.getSession(value.sessionId);
        const record = engine.row(session, { kind: 'input', source: 'asr', text: '正在识别语音…', status: 'running', startedAt: Date.now() });
        try { const transcription = await voice.stop(); Object.assign(record, { text: '语音识别完成', status: 'completed', endedAt: Date.now(), durationMs: Date.now() - record.startedAt, result: { text: transcription.text } }); engine.persist(session, record); result = { text: transcription.text, ...engine.start(value.sessionId, transcription.text, { inputRecordId: record.id }) }; }
        catch (error) { Object.assign(record, { text: error.message, status: 'failed', endedAt: Date.now(), durationMs: Date.now() - record.startedAt }); engine.persist(session, record); throw error; }
      } else if (route.startsWith('/api/')) throw Object.assign(new Error('接口不存在或方法不支持'), { statusCode: 404 });
      else {
        const dist = path.join(root, 'dist'), relative = decodeURIComponent(route).replace(/^\/+/, '') || 'index.html', target = path.resolve(dist, relative);
        if (!target.startsWith(dist + path.sep)) throw new Error('无效路径');
        const file = fs.existsSync(target) && fs.statSync(target).isFile() ? target : path.join(dist, 'index.html');
        if (!fs.existsSync(file)) { res.writeHead(404); res.end('请先 npm run build，或 npm run dev:web'); return; }
        const types = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
        res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream'); fs.createReadStream(file).pipe(res); return;
      }
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(store.safe(result)));
    } catch (error) { if (!res.headersSent) { res.writeHead(error.statusCode || 400, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify({ error: store.safe(error.message) })); } else res.end(); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  const actualPort = server.address().port;
  const close = async () => { voice.cancel(); await engine.stop(); for (const client of clients) client.end(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); };
  return { server, store, engine, knowledge, port: actualPort, close };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const app = await startServer();
  const message = { type: 'ready', port: app.port };
  if (process.send) process.send(message); else console.log(JSON.stringify(message));
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { void app.close().then(() => process.exit(0)); });
  process.on('message', message => { if (message?.type === 'shutdown') void app.close().then(() => process.exit(0)); });
}
