import test from 'node:test';
import assert from 'node:assert/strict';
import { Engine } from '../server/engine.mjs';

test('Pi nested MCP results update task status without losing the original task details', () => {
  const engine = new Engine(null, null, null), session = {};
  const task = { task_type: '放飞前出任务', task_name: '接入检查', parameters: { mode: '原地起飞', takeoff_altitude: 50 } };
  const created = { task_id: 'TASK-test', status: 'draft', task, simulation: true };
  engine.rememberTask(session, { content: [], structuredContent: { content: [], structuredContent: created } });
  assert.equal(session.tasks['TASK-test'].task.task_name, '接入检查');
  engine.rememberTask(session, { structuredContent: { structuredContent: { task_id: 'TASK-test', status: 'executing', simulation: true } } });
  assert.equal(session.tasks['TASK-test'].status, 'executing');
  assert.deepEqual(session.tasks['TASK-test'].task, task);
  engine.rememberTask(session, { content: [{ type: 'text', text: JSON.stringify({ task_id: 'TASK-test', status: 'uploaded' }) }] });
  assert.equal(session.tasks['TASK-test'].status, 'uploaded');
});
