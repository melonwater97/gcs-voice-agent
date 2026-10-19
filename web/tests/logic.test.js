import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encodeWav } from '../src/lib/wav.js';
import { fieldChanges, taskFields } from '../src/lib/tasks.js';
import { nextMsgId } from '../src/lib/msgId.js';
import { audioBase64, makeMessage } from '../src/lib/api.js';

test('WAV 编码为 16kHz 单声道 PCM16，采样值不会溢出', () => {
  const result = encodeWav(new Float32Array([-2, -1, 0, 0.5, 1, 2]));
  const view = new DataView(result);
  assert.equal(new TextDecoder().decode(result.slice(0, 4)), 'RIFF');
  assert.equal(view.getUint32(4, true), result.byteLength - 8);
  assert.equal(view.getUint16(20, true), 1);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 12);
  assert.deepEqual(Array.from({ length: 6 }, (_, i) => view.getInt16(44 + i * 2, true)), [-32768, -32768, 0, 16384, 32767, 32767]);
});

test('参数变化包含嵌套字段的新增、修改和移除', () => {
  const before = { task: { parameters: { takeoff_altitude: 50, formation: { radius: 100, open_angle: 90 } } } };
  const after = { task: { parameters: { takeoff_altitude: 80, formation: { radius: 100, position: { longitude: 120.1 } } } } };
  const changes = fieldChanges(before, after);
  assert.equal(changes.length, 3);
  assert.deepEqual(changes.map(change => change.path), ['parameters.takeoff_altitude', 'parameters.formation.open_angle', 'parameters.formation.position.longitude']);
  assert.equal(changes[0].before, 50);
  assert.equal(changes[0].after, 80);
  assert.equal(changes[0].unit, 'm');
  assert.equal(taskFields(after.task).at(-1).label, '展开经度');
});

test('UUID 消息编号跨页面和重启无需共享计数器，信封关联保持一致', () => {
  const ids = Array.from({ length: 1000 }, () => nextMsgId());
  assert.equal(new Set(ids).size, ids.length);
  for (const id of ids) assert.match(id, /^MSG-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const message = makeMessage('AGENT_INPUT', { text: '上传晨曦任务' }, ids[0], ids[1]);
  assert.deepEqual(Object.keys(message), ['header', 'payload']);
  assert.equal(message.header.msg_id, ids[0]);
  assert.equal(message.header.ref_msg_id, ids[1]);
  assert.deepEqual(message.header.src, { type: 'FRONT', id: 'GS-01' });
  assert.deepEqual(message.header.dst, { type: 'BACKGROUND', id: 'AGENT-01' });
});

test('长录音 Base64 编码后能够完整还原 WAV 字节', async () => {
  const bytes = new Uint8Array(320000);
  for (let index = 0; index < bytes.length; index++) bytes[index] = index % 256;
  const encoded = await audioBase64(new Blob([bytes], { type: 'audio/wav' }));
  assert.deepEqual(Buffer.from(encoded, 'base64'), Buffer.from(bytes));
});
