import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { connectMcp } from '../server/mcp.mjs';
import { startServer } from '../server/index.mjs';
import { prepareRuntimeConfig, saveMcpYaml, managedConfigPath } from '../server/runtime-config.mjs';
import { httpSimulator } from './helpers/http-simulator.mjs';

test('direct Python MCP tasks survive server restarts between create, upload and execute', { skip: !process.env.LH_TEST_PYTHON, timeout: 45000 }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-python-mcp-'));
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const config = { transport: 'stdio', command: process.env.LH_TEST_PYTHON, cwd: root,
    args: ['-m', 'integrations.mcp_server', '--agent-tools'],
    env: { PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
      TASK_OUTPUT_DIR: path.join(dir, 'output'), TASK_STATE_PATH: path.join(dir, 'state.json') } };
  const call = async (name, args) => {
    const connection = await connectMcp(config);
    try {
      assert.deepEqual(connection.tools.map(t => t.name).sort(), ['create_task', 'execute_task', 'upload_task']);
      assert.equal(connection.tools[0].inputSchema.properties.header, undefined);
      const result = await connection.client.callTool({ name, arguments: args });
      assert.notEqual(result.isError, true, JSON.stringify(result));
      return result.structuredContent;
    } finally { await connection.close(); }
  };
  try {
    const created = await call('create_task', { task: { task_type: '放飞前出任务', task_name: 'Python接入回归', parameters: { mode: '原地起飞', takeoff_altitude: 50 } } });
    assert.equal(created.status, 'draft'); assert.equal(created.simulation, true);
    const connection = await connectMcp(config);
    try {
      const premature = await connection.client.callTool({ name: 'execute_task', arguments: { task_id: created.task_id } });
      assert.equal(premature.isError, true, 'Business failures must be MCP errors, not successful envelopes');
      const invalid = await connection.client.callTool({ name: 'create_task', arguments: { task: { task_type: '放飞前出任务', parameters: { mode: '原地起飞' } } } });
      assert.equal(invalid.isError, true, 'Missing altitude must not create a task');
    } finally { await connection.close(); }
    const uploaded = await call('upload_task', { task_id: created.task_id });
    assert.equal(uploaded.status, 'uploaded');
    assert.equal(fs.existsSync(path.join(dir, 'output', `${created.task_id}.json`)), true);
    const executed = await call('execute_task', { task_id: created.task_id });
    assert.equal(executed.status, 'executing'); assert.equal(executed.task_id, created.task_id);
    assert.equal(JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8')).tasks[created.task_id].status, 'executing');
  } finally {
    assert.equal(path.dirname(path.resolve(dir)), path.resolve(os.tmpdir()));
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Pi calls Python MCP and the independent HTTP simulator using validated YAML', { skip: !process.env.LH_TEST_PYTHON, timeout: 45000 }, async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-pi-python-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const baseUrl = await httpSimulator(t, dir);
  let requests = 0;
  const model = http.createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw); requests++;
    const tool = body.tools.find(item => item.function.name.endsWith('__create_task'));
    assert.ok(tool, 'Pi must expose the Python tool to the model');
    assert.equal(tool.function.parameters.properties.header, undefined);
    const done = body.messages.some(message => message.role === 'tool');
    const delta = done ? { role: 'assistant', content: '已创建本地模拟任务。' }
      : { role: 'assistant', tool_calls: [{ index: 0, id: 'python_direct_call', type: 'function', function: { name: tool.function.name,
        arguments: JSON.stringify({ task: { task_type: '放飞前出任务', task_name: 'Pi直连检查', parameters: { mode: '原地起飞', takeoff_altitude: 50 } } }) } }] };
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write(`data: ${JSON.stringify({ id: 'direct-test', object: 'chat.completion.chunk', model: 'test', choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`);
    res.write(`data: ${JSON.stringify({ id: 'direct-test', object: 'chat.completion.chunk', model: 'test', choices: [{ index: 0, delta: {}, finish_reason: done ? 'stop' : 'tool_calls' }] })}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve => model.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => model.close(resolve)));
  const app = await startServer({ port: 0, dataDir: dir });
  t.after(() => app.close());
  app.store.saveConfig({ ...app.store.config,
    model: { ...app.store.config.model, baseUrl: `http://127.0.0.1:${model.address().port}/v1`, model: 'test' },
    mcp: [{ id: 'python-task-simulator', name: 'python_tasks', transport: 'stdio', enabled: true,
      command: process.env.LH_TEST_PYTHON, args: ['-m', 'integrations.mcp_server', '--agent-tools'],
      cwd: fileURLToPath(new URL('../..', import.meta.url)), env: { PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
        TASK_OUTPUT_DIR: path.join(dir, 'output'), TASK_STATE_PATH: path.join(dir, 'state.json') } }] });
  prepareRuntimeConfig(app.store);
  const yaml = `ground_station:\n  base_url: ${baseUrl}\n  simulation: true\n  timeout_seconds: 3\n`;
  await saveMcpYaml(app.store, 'python-task-simulator', yaml);
  const configUrl = `http://127.0.0.1:${app.port}/api/mcp/python-task-simulator/config`;
  const rejected = await fetch(configUrl, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ yaml: 'ground_station:\n  timeout_seconds: 0' }) });
  assert.equal(rejected.status, 400);
  assert.equal(fs.readFileSync(managedConfigPath(app.store), 'utf8'), yaml, 'Invalid YAML must not overwrite the saved configuration');
  const session = app.store.newSession();
  app.engine.start(session.id, '创建原地起飞高度50米的本地模拟任务');
  await app.engine.task;
  const saved = app.store.getSession(session.id), call = saved.records.find(row => row.kind === 'tool');
  assert.equal(requests, 2);
  assert.equal(call?.status, 'completed', JSON.stringify(saved.records.filter(row => row.kind === 'notice')));
  assert.equal(Object.values(saved.tasks || {}).some(task => task.status === 'draft'), true);
  const task = Object.values(saved.tasks)[0];
  assert.equal(task.source, 'groundstation-http-simulator');
  assert.equal(task.simulation, true);
  assert.equal(Object.keys(JSON.parse(fs.readFileSync(path.join(dir, 'http-state.json'), 'utf8')).tasks).length, 1);
  const persistentPid = app.mcp.snapshot('python-task-simulator').pid;
  app.store.saveConfig({ ...app.store.config, workflows: [{ id: 'http-lifecycle', name: '模拟上传与执行', enabled: true, steps: [
    { id: 'upload', type: 'tool', tool: 'mcp__python_tasks__upload_task', args: { task_id: task.task_id } },
    { id: 'execute', type: 'tool', tool: 'mcp__python_tasks__execute_task', args: { task_id: task.task_id } },
  ] }] });
  app.engine.start(session.id, '运行模拟上传与执行工作流', { workflowId: 'http-lifecycle', input: {} });
  await app.engine.task;
  assert.equal(app.store.getSession(session.id).tasks[task.task_id].status, 'executing');
  assert.equal(app.mcp.snapshot('python-task-simulator').pid, persistentPid, 'Pi and workflow steps must share one process');
  const probe = await fetch(`http://127.0.0.1:${app.port}/api/diagnostics`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'mcp', id: 'python-task-simulator' }) }).then(response => response.json());
  assert.equal(probe.connected, true); assert.equal(probe.http.reachable, true);
  assert.equal(app.mcp.snapshot('python-task-simulator').pid, persistentPid);
  const reload = await fetch(configUrl, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ yaml }) });
  assert.equal(reload.status, 200);
  assert.notEqual(app.mcp.snapshot('python-task-simulator').pid, persistentPid, 'Saving YAML must restart the running process with the new configuration');
  assert.equal(Object.keys(JSON.parse(fs.readFileSync(path.join(dir, 'http-state.json'), 'utf8')).tasks).length, 1, 'Connection tests must not create tasks');
  assert.match(saved.records.findLast(row => row.kind === 'assistant').text, /本地模拟/);
});
