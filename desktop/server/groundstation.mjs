import path from 'node:path';
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import Ajv from 'ajv';
import { atomicJSON, readJSON, uid } from './store.mjs';

const number = { type: 'number', exclusiveMinimum: 0 };
const position = { type: 'object', additionalProperties: false, required: ['longitude', 'latitude'], properties: { longitude: { type: 'number', minimum: -180, maximum: 180 }, latitude: { type: 'number', minimum: -90, maximum: 90 } } };
const formation = { oneOf: [
  { type: 'object', additionalProperties: false, required: ['formation_type', 'open_angle', 'radius', 'position'], properties: { formation_type: { const: 'C型' }, open_angle: { ...number, maximum: 360 }, radius: number, position } },
  { type: 'object', additionalProperties: false, required: ['formation_type', 'formation_length', 'formation_width', 'position'], properties: { formation_type: { const: 'U型' }, formation_length: number, formation_width: number, position } },
] };
export const taskSchema = { type: 'object', additionalProperties: false, required: ['task_type', 'parameters'], properties: {
  task_type: { const: '放飞前出任务' }, task_name: { type: ['string', 'null'], minLength: 1, maxLength: 64, pattern: '\\S' },
  parameters: { oneOf: [
    { type: 'object', additionalProperties: false, required: ['mode', 'takeoff_altitude'], properties: { mode: { const: '原地起飞' }, takeoff_altitude: number } },
    { type: 'object', additionalProperties: false, required: ['mode', 'takeoff_altitude', 'formation'], properties: { mode: { const: '在指定位置展开' }, takeoff_altitude: number, formation } },
  ] },
} };
export const groundTools = [
  { name: 'create_task', description: '创建放飞前出任务草稿。缺少模式、高度或编队参数必须先澄清；创建不会自动上传或执行。', inputSchema: { type: 'object', additionalProperties: false, required: ['task'], properties: { task: taskSchema } } },
  { name: 'upload_task', description: '上传指定的草稿任务。必须使用已创建任务的唯一 task_id，不使用名称代替。', inputSchema: { type: 'object', additionalProperties: false, required: ['task_id'], properties: { task_id: { type: 'string', pattern: '^TASK-[0-9a-f]{32}$' } } } },
  { name: 'execute_task', description: '启动指定的已上传任务。只在用户明确要求执行或明确运行的工作流包含此步骤时调用。', inputSchema: { type: 'object', additionalProperties: false, required: ['task_id'], properties: { task_id: { type: 'string', pattern: '^TASK-[0-9a-f]{32}$' } } } },
];
const ajv = new Ajv({ allErrors: true, strict: false });
const validators = new Map(groundTools.map(t => [t.name, ajv.compile(t.inputSchema)]));
export function validateTool(name, args) {
  const valid = validators.get(name);
  if (!valid) throw new Error('未知地面站工具');
  if (!valid(args)) throw new Error('任务参数无效：' + ajv.errorsText(valid.errors, { separator: '；' }));
}
function canonical(value) { return value && typeof value === 'object' ? Array.isArray(value) ? value.map(canonical) : Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value; }
export class GroundStation {
  constructor(config, dir) { this.config = config; this.dir = dir; fs.mkdirSync(dir, { recursive: true }); }
  async call(name, args, { signal } = {}) {
    validateTool(name, args);
    if (!this.config.enabled || !this.config.baseUrl) throw new Error('请在设置中启用地面站并填写 HTTP 服务地址');
    const body = { type: name, ...args }, fingerprint = createHash('sha256').update(JSON.stringify(canonical(body))).digest('hex');
    const file = path.join(this.dir, `${fingerprint}.json`), previous = readJSON(file, null);
    const uncertain = previous && ['sending', 'unknown'].includes(previous.status);
    if (uncertain && Date.now() - previous.createdAt > 24 * 3600 * 1000) throw new Error(`原请求 ${previous.envelope.header.msg_id} 已超过 24 小时，请先在地面站核对结果`);
    const envelope = uncertain ? previous.envelope : { header: { msg_id: `MSG-${uid()}`, msg_type: 'TASK_COMMAND', version: '2.0.0', src: { type: 'BACKGROUND', id: 'AGENT-01' }, dst: { type: 'FRONT', id: 'GS-01' } }, payload: body };
    const entry = { status: 'sending', createdAt: uncertain ? previous.createdAt : Date.now(), envelope };
    atomicJSON(file, entry);
    let sent = false;
    try {
      signal?.throwIfAborted();
      sent = true;
      const response = await fetch(this.config.baseUrl.replace(/\/$/, '') + '/api/tasks', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...(this.config.token ? { Authorization: `Bearer ${this.config.token}` } : {}) },
        body: JSON.stringify(envelope), signal: AbortSignal.any([AbortSignal.timeout(this.config.timeoutSeconds * 1000), ...(signal ? [signal] : [])]),
      });
      const result = await response.json();
      if (result.header?.msg_type !== 'TASK_RESULT' || result.header.version !== '2.0.0' || result.header.src?.id !== 'GS-01' || result.header.src.type !== 'FRONT' || result.header.dst?.id !== 'AGENT-01' || result.header.dst.type !== 'BACKGROUND' || !Number.isInteger(result.payload?.code) || result.payload.code !== response.status) throw new Error('地面站回执格式或路由不符合约定');
      if (!response.ok) {
        if (response.status >= 500) throw new Error(result.payload.message || '地面站服务错误');
        entry.status = 'failed'; entry.result = result; atomicJSON(file, entry);
        throw Object.assign(new Error(result.payload.message || '地面站拒绝请求'), { confirmed: true });
      }
      const payload = result.payload;
      const expected = { create_task: 'draft', upload_task: 'uploaded', execute_task: 'executing' }[name];
      if (payload.type !== name || payload.status !== expected || !/^TASK-[0-9a-f]{32}$/.test(payload.task_id || '') || !payload.task || (args.task_id && payload.task_id !== args.task_id)) throw new Error('地面站成功回执缺少匹配的任务、编号或状态');
      validateTool('create_task', { task: payload.task });
      entry.status = 'completed'; entry.result = result; atomicJSON(file, entry);
      return { ...payload, request_id: envelope.header.msg_id };
    } catch (error) {
      if (!error.confirmed) {
        entry.status = sent ? 'unknown' : 'cancelled'; atomicJSON(file, entry);
        if (sent) throw Object.assign(new Error(`结果待确认：${error.message}。原请求 ${envelope.header.msg_id}，核对或以原编号重试。`), { unknown: true, requestId: envelope.header.msg_id });
      }
      throw error;
    }
  }
}
