import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Store } from '../server/store.mjs';
import { McpRuntime } from '../server/mcp-runtime.mjs';

const fakeTool = { name: 'work', description: 'A test operation', inputSchema: { type: 'object', properties: {} } };
function configure(store, patch = {}) {
  store.saveConfig({ ...store.config, mcp: [{ id: 'test-mcp', name: 'test_tools', transport: 'stdio', command: 'unused', args: [], env: {}, enabled: false, ...patch }] });
}

test('pause drains the current call, rejects subsequent calls, and does not restart on the next Pi turn', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-mcp-drain-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new Store(dir); configure(store);
  let starts = 0, closes = 0, finish;
  const runtime = new McpRuntime(store, () => {}, { connect: async () => {
    starts++;
    return { tools: [fakeTool], pid: 123, listTools: async () => [fakeTool], close: async () => { closes++; },
      client: { callTool: () => new Promise(resolve => { finish = () => resolve({ content: [{ type: 'text', text: 'finished' }] }); }) } };
  } });
  t.after(() => runtime.close());
  await Promise.all([runtime.start('test-mcp'), runtime.start('test-mcp')]);
  assert.equal(starts, 1, 'Concurrent starts share one connection');
  const call = runtime.call('test-mcp', 'work', {});
  assert.equal(runtime.snapshot('test-mcp').activeCalls, 1);
  const paused = await runtime.pause('test-mcp');
  assert.equal(paused.status, 'pausing'); assert.equal(closes, 0);
  await runtime.sync(); assert.equal(closes, 0, 'Refreshing configuration must not close an active call');
  await assert.rejects(runtime.call('test-mcp', 'work', {}), /已暂停/);
  await assert.rejects(runtime.start('test-mcp'), /正在暂停/);
  assert.equal(store.config.mcp[0].enabled, false);
  finish(); await call;
  assert.equal(runtime.snapshot('test-mcp').status, 'paused'); assert.equal(closes, 1);
  assert.deepEqual(await runtime.piTools(), []);
  assert.equal(starts, 1, 'Pi must not silently restart paused services');
  const reloaded = new McpRuntime(store);
  assert.equal(reloaded.snapshot('test-mcp').tools[0].name, 'work', 'Paused services retain the discovered catalog');
  await reloaded.close();
});

test('remote tests only connect and disconnect; unexpected disconnects surface as failed state', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-mcp-remote-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new Store(dir); configure(store, { transport: 'http', url: 'http://mcp.example/mcp' });
  let disconnected = 0, onClose;
  const runtime = new McpRuntime(store, () => {}, { connect: async (_config, callbacks = {}) => {
    onClose = callbacks.onClose;
    return { tools: [fakeTool], pid: null, client: {}, listTools: async () => [fakeTool], close: async () => { disconnected++; } };
  } });
  t.after(() => runtime.close());
  await runtime.test('test-mcp');
  assert.equal(runtime.snapshot('test-mcp').status, 'paused');
  assert.equal(store.config.mcp[0].enabled, false); assert.equal(disconnected, 1);
  await runtime.start('test-mcp');
  assert.equal(runtime.snapshot('test-mcp').pid, null);
  onClose();
  assert.equal(runtime.snapshot('test-mcp').status, 'failed');
  assert.match(runtime.snapshot('test-mcp').error, /连接已断开/);
  await runtime.start('test-mcp');
  await runtime.pause('test-mcp');
  assert.equal(runtime.snapshot('test-mcp').status, 'paused');
});

test('local Python MCP has a persistent real PID shared by Pi tools, testing, pause and restart', { skip: !process.env.LH_TEST_PYTHON, timeout: 30000 }, async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-mcp-process-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new Store(dir);
  configure(store, { command: process.env.LH_TEST_PYTHON, args: ['-m', 'integrations.mcp_server', '--agent-tools'],
    cwd: fileURLToPath(new URL('../..', import.meta.url)), env: { PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', TASK_STATE_PATH: path.join(dir, 'state.json') } });
  const runtime = new McpRuntime(store); t.after(() => runtime.close());
  const started = await runtime.start('test-mcp'), pid = started.pid;
  assert.ok(pid); assert.doesNotThrow(() => process.kill(pid, 0));
  const tools = await runtime.piTools();
  assert.equal(tools.length, 3); assert.equal(runtime.snapshot('test-mcp').pid, pid);
  const created = await tools.find(tool => tool.name.endsWith('__create_task')).execute('test-call', { task: { task_type: '放飞前出任务', parameters: { mode: '原地起飞', takeoff_altitude: 50 } } });
  assert.equal(created.details.structuredContent.status, 'draft');
  await runtime.test('test-mcp'); assert.equal(runtime.snapshot('test-mcp').pid, pid);
  await runtime.pause('test-mcp');
  assert.throws(() => process.kill(pid, 0), 'Pausing must close the real local process');
  const pausedTest = await runtime.test('test-mcp');
  assert.equal(pausedTest.connected, true); assert.equal(runtime.snapshot('test-mcp').pid, null);
  assert.equal(store.config.mcp[0].enabled, false);
  const restarted = await runtime.start('test-mcp'); assert.notEqual(restarted.pid, pid);
});
