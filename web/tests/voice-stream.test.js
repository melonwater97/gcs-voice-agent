import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeMessage, readVoiceStream } from '../src/lib/api.js';

const request = makeMessage('VOICE_INPUT', { audio_format: 'wav', audio_base64: 'test' });
const AGENT = { type: 'BACKGROUND', id: 'AGENT-01' };
const FRONT = { type: 'FRONT', id: 'GS-01' };
function frame(msgType, payload) {
  const base = makeMessage(msgType, payload);
  return { ...base, header: { ...base.header, src: AGENT, dst: FRONT } };
}
const transcript = frame('ASR_OUTPUT', { text: '起飞高度五十米' });
const result = frame('AGENT_OUTPUT', { type: 'clarify', text: '请补充任务类型。' });
const encode = value => new TextEncoder().encode(JSON.stringify(value) + '\n');
const response = stream => new Response(stream, { headers: { 'Content-Type': 'application/x-ndjson' } });

test('转写按网络分块解码中文，模型结果到达前就回调前端', async () => {
  let control;
  const stream = new ReadableStream({ start(controller) { control = controller; } });
  let delivered;
  const seen = new Promise(resolve => { delivered = resolve; });
  let completed = false;
  const pending = readVoiceStream(response(stream), request, message => delivered(message));
  pending.then(() => { completed = true; });
  const bytes = encode(transcript);
  for (let index = 0; index < bytes.length; index += 2) control.enqueue(bytes.slice(index, index + 2));
  assert.deepEqual(await seen, transcript);
  assert.equal(completed, false);
  control.enqueue(encode(result));
  control.close();
  assert.deepEqual(await pending, result);
});

test('转写后的流中断保留已交付文本，并允许用原请求重试', async () => {
  const seen = [];
  const stream = new ReadableStream({ start(controller) { controller.enqueue(encode(transcript)); controller.close(); } });
  await assert.rejects(readVoiceStream(response(stream), request, msg => seen.push(msg)), error => {
    assert.equal(error.retryable, true);
    return /尚未收到处理结果/.test(error.message);
  });
  assert.deepEqual(seen, [transcript]);
});

test('校验每帧路由，拒绝没有转写的结果', async () => {
  for (const invalid of [result, { ...transcript, header: { ...transcript.header, src: FRONT } }]) {
    const stream = new ReadableStream({ start(controller) { controller.enqueue(encode(invalid)); controller.close(); } });
    await assert.rejects(readVoiceStream(response(stream), request), error => error.retryable === true);
  }
});

test('已返回转写后仍能读取同一连接上的 ERROR 信封', async () => {
  const stream = new ReadableStream({ start(controller) {
    controller.enqueue(encode(transcript));
    controller.enqueue(encode(frame('ERROR', { error: 'Agent 等待超时' })));
    controller.close();
  } });
  await assert.rejects(readVoiceStream(response(stream), request), /Agent 等待超时/);
});
