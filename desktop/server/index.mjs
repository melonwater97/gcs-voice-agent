import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Store, mergeMasked, validateSettings, uid } from './store.mjs';
import { Knowledge, readSkill } from './knowledge.mjs';
import { Engine } from './engine.mjs';
import { Voice } from './voice.mjs';
import { connectMcp } from './mcp.mjs';
import { McpRuntime } from './mcp-runtime.mjs';
import { FlightCoordinator } from './flight-coordinator.mjs';
import { scenarioFor } from './scenarios.mjs';
import { setupFlightRuntime } from './flight-runtime-config.mjs';
import { generateFlightReport } from './flight-report.mjs';
import { validateWorkflow, starterWorkflow } from './workflows.mjs';
import { prepareRuntimeConfig, managedMcpId, readMcpYaml, saveMcpYaml } from './runtime-config.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
export async function startServer({ port = Number(process.env.DESKTOP_PORT || 18301), dataDir = process.env.LH_DATA_DIR || path.join(root, '.data'), token = process.env.LH_BACKEND_TOKEN || '', encryptionKey = process.env.LH_CREDENTIAL_KEY } = {}) {
  const store = new Store(dataDir, encryptionKey), knowledge = new Knowledge(store), clients = new Set();
  prepareRuntimeConfig(store);
  const notify = event => { const line = `data: ${JSON.stringify(store.safe(event))}\n\n`; for (const client of clients) client.write(line); };
  const mcp = new McpRuntime(store, notify), flights = new FlightCoordinator(store, mcp, notify), engine = new Engine(store, knowledge, notify, mcp, flights), voice = new Voice(store);
  flights.generateReport = id => generateFlightReport(flights, id);
  const monitorJobs = new Map(), autoReports = new Set();
  const monitor = setInterval(() => {
    for (const flight of flights.list()) {
      if (monitorJobs.has(flight.id) || !['monitoring', 'intervention_required', 'unknown', 'landed'].includes(flight.phase) || (flight.phase === 'landed' && autoReports.has(flight.id))) continue;
      const job = (flight.phase === 'landed' ? Promise.resolve(flight) : flights.poll(flight.id)).then(async current => {
        if (current.phase === 'landed' && !autoReports.has(current.id)) {
          autoReports.add(current.id); await flights.generateReport(current.id);
        }
      }).catch(() => { /* State and errors are persisted for manual recovery. */ }).finally(() => monitorJobs.delete(flight.id));
      monitorJobs.set(flight.id, job);
    }
  }, 3000);
  monitor.unref();
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
        // Use the bound port (port: 0 selects a random port) and support both local hostnames.
        const allowedOrigins = [server.address().port, 5180, 5181].flatMap(localPort =>
          [`http://127.0.0.1:${localPort}`, `http://localhost:${localPort}`]);
        if (origin && !allowedOrigins.includes(origin)) throw Object.assign(new Error('不支持的请求来源'), { statusCode: 403 });
        if (token && req.headers.authorization !== `Bearer ${token}`) throw Object.assign(new Error('未授权'), { statusCode: 401 });
      }
      let result;
      if (route === '/api/events' && req.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' }); res.write(': connected\n\n');
        clients.add(res); const heartbeat = setInterval(() => res.write(': heartbeat\n\n'), 15000);
        req.on('close', () => { clearInterval(heartbeat); clients.delete(res); }); return;
      }
      if (route === '/api/state' && req.method === 'GET') result = { config: store.publicConfig(), mcpRuntime: mcp.snapshots(), activeFlights: flights.active().map(flight => ({ id: flight.id, phase: flight.phase })), sessions: store.listSessions(), knowledge: knowledge.list(), active: engine.active ? { sessionId: engine.active.sessionId, runId: engine.active.runId } : null, voice: voice.phase, version: '0.1.0', engine: 'Pi 1.4.0' };
      else if (route === '/api/settings' && req.method === 'PUT') {
        idle(); if (flights.active().length) throw new Error('请先结束或核对正在执行的仿真，再修改连接和约束'); const config = await body(req); for (const flow of config.workflows || []) validateWorkflow(flow);
        store.saveConfig(config); prepareRuntimeConfig(store); await mcp.sync(); result = store.publicConfig(); voice.cancel(); notify({ type: 'settings' });
      } else if (route === '/api/scenarios/switch' && req.method === 'POST') {
        idle(); const value = await body(req); scenarioFor(store.config, value.id);
        if (flights.active().length) throw new Error('请先处理正在执行或待核对的仿真飞行');
        store.saveConfig({ ...store.config, activeScenarioId: value.id }); result = store.newSession(); notify({ type: 'settings' });
      } else if (route === '/api/sessions' && req.method === 'POST') { idle(); result = store.newSession(); }
      else if (/^\/api\/flights\/session\/[\w-]+$/.test(route) && req.method === 'GET') result = flights.forSession(route.split('/').at(-1));
      else if (route === '/api/flights' && req.method === 'POST') { idle(); const value = await body(req); result = flights.create(store.getSession(value.sessionId), { newRun: value.newRun === true }); }
      else if (/^\/api\/flights\/[\w-]+\/(sync|request|poll|preview)$/.test(route) && req.method === 'POST') {
        const [, , , id, operation] = route.split('/'), value = await body(req); if (operation !== 'poll') idle();
        if (operation === 'sync') result = await flights.sync(id);
        if (operation === 'poll') result = await flights.poll(id);
        if (operation === 'preview') result = await flights.preview(id, value.plan, value.expectedRevision);
        if (operation === 'request') result = await flights.propose(id, value.action, value.payload);
      }
      else if (route === '/api/flight/setup' && req.method === 'POST') { idle(); result = setupFlightRuntime(store); notify({ type: 'settings' }); }
      else if (/^\/api\/flights\/[\w-]+\/report$/.test(route) && req.method === 'POST') { idle(); result = await generateFlightReport(flights, route.split('/')[3]); }
      else if (/^\/api\/flights\/[\w-]+\/reconcile$/.test(route) && req.method === 'POST') {
        idle(); if (!token && !req.headers.origin) throw Object.assign(new Error('核对恢复需要界面操作'), { statusCode: 403 }); result = await flights.reconcile(route.split('/')[3]);
      }
      else if (/^\/api\/flights\/[\w-]+\/artifacts\/(md|docx|log)$/.test(route) && req.method === 'GET') {
        const parts = route.split('/'), flight = flights.get(parts[3]), file = flight.report?.[parts[5]];
        const artifactRoot = path.join(flights.dir, flight.id, 'artifacts');
        if (!file || !path.resolve(file).startsWith(artifactRoot + path.sep) || !fs.existsSync(file)) throw new Error('报告文件尚未生成');
        res.writeHead(200, { 'Content-Type': parts[5] === 'docx' ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' : 'application/octet-stream',
          'Content-Disposition': `attachment; filename="${path.basename(file)}"` }); fs.createReadStream(file).pipe(res); return;
      }
      else if (/^\/api\/flights\/[\w-]+\/approvals\/[\w-]+$/.test(route) && req.method === 'POST') {
        idle();
        if (!token && !req.headers.origin) throw Object.assign(new Error('审批需要来自助手界面的明确操作'), { statusCode: 403 });
        const parts = route.split('/'), value = await body(req);
        result = await flights.decide(parts[3], parts[5], value.decision, { human: true });
        const session = store.getSession(result.sessionId); engine.row(session, { kind: 'notice', text: `界面审批：${value.decision === 'approve' ? '批准' : '拒绝'} · ${result.approvals.find(a => a.id === parts[5])?.action}`, status: 'completed' });
      }
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
      else if (route === '/api/workflows/starter' && req.method === 'POST') { idle(); const flow = { ...starterWorkflow, id: uid() }; store.saveConfig({ ...store.config, workflows: [...store.config.workflows, flow] }); prepareRuntimeConfig(store); result = store.publicConfig().workflows; }
      else if (route === '/api/mcp/groundstation' && req.method === 'POST') {
        idle();
        if (store.config.mcp.some(service => service.id === managedMcpId)) throw new Error('地面站 MCP 服务已存在');
        store.saveConfig({ ...store.config, mcp: [...store.config.mcp, { id: managedMcpId,
          name: store.config.mcp.some(s => s.name === 'groundstation') ? 'groundstation_tools' : 'groundstation',
          enabled: false, transport: 'stdio', command: store.config.voice.python, args: [], env: {} }] });
        prepareRuntimeConfig(store); result = store.publicConfig(); notify({ type: 'settings' });
      }
      else if (/^\/api\/mcp\/[\w-]+\/config$/.test(route) && req.method === 'GET') result = readMcpYaml(store, route.split('/')[3]);
      else if (/^\/api\/mcp\/[\w-]+\/config$/.test(route) && req.method === 'PUT') {
        idle(); const value = await body(req), id = route.split('/')[3]; result = await saveMcpYaml(store, id, value.yaml);
        await mcp.restart(id); notify({ type: 'settings' });
      }
      else if (route === '/api/mcp/runtime' && req.method === 'GET') result = mcp.snapshots();
      else if (/^\/api\/mcp\/[\w-]+\/(start|pause|test)$/.test(route) && req.method === 'POST') {
        const [, , , id, operation] = route.split('/');
        if (operation === 'pause' && flights.active().some(flight => flights.profile(flight).flight.serviceId === id)) throw new Error('该服务正在执行或监测仿真，请先人工接管并核对落地状态');
        result = await mcp[operation](id); notify({ type: 'settings' });
      }
      else if (route === '/api/diagnostics' && req.method === 'POST') {
        const value = await body(req);
        if (value.type === 'voice') result = await voice.request('probe');
        else if (value.type === 'asr') result = await voice.request('warmup');
        else if (value.type === 'mcp') {
          const draft = mergeMasked(value.config || store.config.mcp.find(s => s.id === value.id), store.config.mcp.find(s => s.id === value.id));
          if (!draft) throw new Error('MCP 配置不存在');
          validateSettings({ ...store.config, mcp: [draft] });
          const saved = store.config.mcp.find(service => service.id === value.id);
          if (!value.config || JSON.stringify(draft) === JSON.stringify(saved)) result = await mcp.test(value.id);
          else {
            const connection = await connectMcp(draft);
            try { result = { connected: true, tools: connection.tools }; } finally { await connection.close(); }
          }
        } else if (value.type === 'model') {
          const c = store.config;
          const response = await fetch(c.model.baseUrl.replace(/\/chat\/completions\/?$/, '').replace(/\/$/, '') + '/models', { headers: c.model.apiKey ? { Authorization: `Bearer ${c.model.apiKey}` } : {}, signal: AbortSignal.timeout(8000) });
          if (value.type === 'model' && !response.ok) throw new Error(`模型服务返回 HTTP ${response.status}`);
          result = { reachable: true, status: response.status, message: '模型服务可访问；工具调用需通过实际对话验证' };
        } else throw new Error('未知诊断类型');
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
  void mcp.sync();
  const close = async () => { clearInterval(monitor); voice.cancel(); await engine.stop(); await Promise.allSettled(monitorJobs.values()); await mcp.close(); for (const client of clients) client.end(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); };
  return { server, store, engine, knowledge, mcp, flights, port: actualPort, close };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const app = await startServer();
  const message = { type: 'ready', port: app.port };
  if (process.send) process.send(message); else console.log(JSON.stringify(message));
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { void app.close().then(() => process.exit(0)); });
  process.on('message', message => { if (message?.type === 'shutdown') void app.close().then(() => process.exit(0)); });
}
