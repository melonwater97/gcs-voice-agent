import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { connectMcp } from '../server/mcp.mjs';

test('original Python MCP tasks survive bridge restarts between create, upload and execute', { skip: !process.env.LH_TEST_PYTHON, timeout: 45000 }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'linghang-python-mcp-'));
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const config = { transport: 'stdio', command: process.execPath,
    args: [fileURLToPath(new URL('../server/python-mcp.mjs', import.meta.url))],
    env: { LH_LEGACY_PYTHON: process.env.LH_TEST_PYTHON, LH_PROJECT_ROOT: root,
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
