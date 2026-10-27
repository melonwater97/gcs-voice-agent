import test from 'node:test';
import assert from 'node:assert/strict';
import { projectTrajectory, visibleItems, matchesRow, timelineData, clampViewport } from '../src/lib/trajectory.js';

const base = 1800000000000;
const fixture = [
  { id: 'run1', kind: 'run', runId: 'r1', turnNumber: 1, durationMs: 1800, startedAt: base, status: 'completed', stepCount: 2 },
  { id: 'u1', kind: 'user', runId: 'r1', turnNumber: 1, text: '创建任务', at: new Date(base).toISOString(), status: 'completed' },
  { id: 'a1', kind: 'assistant', runId: 'r1', turnNumber: 1, stepNumber: 1, text: '正在创建', startedAt: base + 20, durationMs: 500, usage: { totalTokens: 125 }, status: 'completed' },
  { id: 't1', kind: 'tool', runId: 'r1', turnNumber: 1, stepNumber: 1, name: 'create_task', callId: 'call1', parentRecordId: 'a1', args: { name: '晨曦' }, startedAt: base + 550, durationMs: 450, status: 'completed' },
  { id: 't2', kind: 'tool', runId: 'r1', turnNumber: 1, stepNumber: 1, name: 'check', callId: 'call2', parentCallId: 'call1', result: { text: 'unique-result' }, startedAt: base + 600, durationMs: 150, status: 'failed' },
  { id: 'a2', kind: 'assistant', runId: 'r1', turnNumber: 1, stepNumber: 2, text: '已完成', startedAt: base + 1100, durationMs: 700, status: 'completed' },
  { id: 'u2', kind: 'user', runId: 'r2', text: '第二轮', at: new Date(base + 3000).toISOString(), status: 'completed' },
  { id: 'a3', kind: 'assistant', runId: 'r2', text: '旧记录没有计时', at: new Date(base + 3100).toISOString(), status: 'completed' },
];
test('trajectory projection retains recorded turns, model steps and nested tool ancestry', () => {
  const data = projectTrajectory(fixture);
  assert.equal(data.groups.length, 2); assert.equal(data.groups[0].steps, 2); assert.equal(data.groups[0].calls, 2);
  assert.equal(data.byId.get('t2').depth, 2); assert.deepEqual(data.byId.get('t2').ancestors, ['a1', 't1']);
  assert.equal(data.groups[0].tokens, 125); assert.equal(data.byId.get('a3').durationMs, undefined);
  assert.equal(data.byId.get('a3').endTime, data.byId.get('a3').startedAt);
  assert.equal(data.groups[1].number, 2);
});
test('search and time overlap filters inspect full results even inside collapsed calls', () => {
  const data = projectTrajectory(fixture), options = { query: 'unique-result', showTurns: true, showCalls: true };
  const items = visibleItems(data, options, new Set(['r1']), new Set(['a1']));
  assert.equal(items.filter(i => i.type === 'record')[0].id, 't2');
  assert.equal(matchesRow(data.byId.get('t1'), { timeRange: [base + 800, base + 900] }), true);
  assert.equal(matchesRow(data.byId.get('t2'), { status: 'errors' }), true);
  assert.equal(matchesRow(data.byId.get('a3'), { timeRange: [base, base + 1000] }), false);
});
test('folded rounds and calls remain distinct and viewport zoom stays in recorded bounds', () => {
  const data = projectTrajectory(fixture);
  const folded = visibleItems(data, { showTurns: true, showCalls: true }, new Set(['r1']));
  assert.equal(folded.some(i => i.id === 't1'), false); assert.equal(folded.some(i => i.id === 'u2'), true);
  const flat = visibleItems(data, { showTurns: false, showCalls: false }, new Set(['r1']), new Set(['a1']));
  assert.equal(flat.length, 7);
  assert.deepEqual(clampViewport([-100, 300], [0, 1000]), [0, 400]);
  assert.deepEqual(clampViewport([900, 1300], [0, 1000]), [600, 1000]);
  assert.equal(timelineData(data.rows).records.length, 7);
});
