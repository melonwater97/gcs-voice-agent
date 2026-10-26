import { test } from 'node:test';
import assert from 'node:assert/strict';
import { getRecordStatus, makeMessage, startRecord, stopRecord } from '../src/lib/api.js';

const AGENT = { type: 'BACKGROUND', id: 'AGENT-01' };
const FRONT = { type: 'FRONT', id: 'GS-01' };
const MSG = 'MSG-550e8400-e29b-41d4-a716-446655440000';

// 所有 Agent 响应都是 AGENT_OUTPUT 信封，种类由 payload.type 区分。
function envelope(payload) {
  return { header: { msg_id: makeMessage('AGENT_OUTPUT', payload).header.msg_id, msg_type: 'AGENT_OUTPUT',
    version: '1.0.0', src: AGENT, dst: FRONT }, payload };
}
function ndjson(frames) {
  return new Response(frames.map(frame => JSON.stringify(frame) + '\n').join(''),
    { headers: { 'Content-Type': 'application/x-ndjson' } });
}
async function withFetch(handler, run) {
  const original = globalThis.fetch;
  globalThis.fetch = handler;
  try { return await run(); } finally { globalThis.fetch = original; }
}

test('开始录音发送 RECORD_COMMAND 和 record_start 并校验监听状态信封', async () => {
  const request = makeMessage('RECORD_COMMAND', { type: 'record_start' }, MSG);
  await withFetch(async (url, options) => {
    assert.equal(url, '/api/record');
    assert.deepEqual(JSON.parse(options.body), request);
    return Response.json(envelope({ type: 'record_start', code: 200, message: '开始监听' }));
  }, async () => {
    const frame = await startRecord(MSG);
    assert.deepEqual(frame.payload, { type: 'record_start', code: 200, message: '开始监听' });
  });
});

test('录音进度查询校验快照信封', async () => {
  const request = makeMessage('RECORD_STATUS', {}, MSG);
  await withFetch(async (url, options) => {
    assert.equal(url, '/api/record/status');
    assert.deepEqual(JSON.parse(options.body), request);
    return Response.json(envelope({ type: 'record_status',
      listening: true, level: 0.4, seconds: 1.2, max_seconds: 90 }));
  }, async () => {
    const frame = await getRecordStatus(MSG);
    assert.deepEqual(frame.payload, { type: 'record_status',
      listening: true, level: 0.4, seconds: 1.2, max_seconds: 90 });
  });
});

test('结束录音发送 RECORD_COMMAND 和 record_stop，复用流式解析先转写后结果', async () => {
  const request = makeMessage('RECORD_COMMAND', { type: 'record_stop' }, MSG);
  const transcript = envelope({ type: 'asr', text: '起飞高度五十米' });
  const result = envelope({ type: 'clarify', text: '请补充任务类型。' });
  const seen = [];
  await withFetch(async (url, options) => {
    assert.equal(url, '/api/record');
    assert.deepEqual(JSON.parse(options.body), request);
    return ndjson([transcript, result]);
  }, async () => {
    const frame = await stopRecord(MSG, message => seen.push(message));
    assert.deepEqual(seen, [transcript]);
    assert.deepEqual(frame, result);
  });
});

test('结束录音缺少转写帧时按可重试错误拒绝', async () => {
  const result = envelope({ type: 'clarify', text: '请补充任务类型。' });
  await withFetch(async () => ndjson([result]), async () => {
    await assert.rejects(stopRecord(MSG), error => {
      assert.equal(error.retryable, true);
      return /缺少语音转写/.test(error.message);
    });
  });
});

test('录音响应路由或信封无效时拒绝', async () => {
  const wrongRoute = envelope({ type: 'record_status', listening: true });
  wrongRoute.header.src = FRONT;
  const legacyType = envelope({ type: 'record_status', listening: true });
  legacyType.header.msg_type = 'RECORD_STATUS_OUTPUT';
  for (const invalid of [wrongRoute, legacyType]) {
    await withFetch(async () => Response.json(invalid), async () => {
      await assert.rejects(getRecordStatus(MSG), error => error.retryable === true);
    });
  }
});
