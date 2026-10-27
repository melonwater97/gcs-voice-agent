const forbidden = new Set(['__proto__', 'prototype', 'constructor']);
function lookup(context, name) {
  let value = context;
  for (const key of name.split('.')) {
    if (forbidden.has(key) || !value || !Object.hasOwn(value, key)) throw new Error(`变量不存在：${name}`);
    value = value[key];
  }
  return value;
}
export function interpolate(value, context) {
  if (typeof value === 'string') {
    const exact = /^\{\{\s*([\w.]+)\s*\}\}$/.exec(value);
    if (exact) return structuredClone(lookup(context, exact[1]));
    return value.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_, key) => {
      const v = lookup(context, key); return typeof v === 'object' ? JSON.stringify(v) : String(v);
    });
  }
  if (Array.isArray(value)) return value.map(v => interpolate(v, context));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, interpolate(v, context)]));
  return value;
}
export function validateWorkflow(flow) {
  if (!flow?.name?.trim() || !Array.isArray(flow.steps) || flow.steps.length < 1 || flow.steps.length > 30) throw new Error('工作流需要名称及 1–30 个步骤');
  const seen = new Set();
  for (const step of flow.steps) {
    if (!/^[a-zA-Z][\w]{0,40}$/.test(step.id || '') || forbidden.has(step.id) || seen.has(step.id)) throw new Error('步骤编号应为唯一的英文字母/数字/下划线');
    if (!['tool', 'knowledge', 'agent', 'skill', 'output'].includes(step.type)) throw new Error('未知工作流步骤类型');
    if (step.type === 'tool' && !step.tool) throw new Error('工具步骤需要 tool');
    if (step.type === 'agent' && !step.prompt) throw new Error('Agent 步骤需要 prompt');
    if (step.timeoutSeconds !== undefined && (!(step.timeoutSeconds >= 1) || step.timeoutSeconds > 300)) throw new Error('步骤超时应为 1–300 秒');
    for (const match of JSON.stringify(step).matchAll(/\{\{\s*steps\.([\w]+)\./g)) if (!seen.has(match[1])) throw new Error(`步骤 ${step.id} 引用了尚未完成的步骤 ${match[1]}`);
    seen.add(step.id);
  }
  return flow;
}
export async function runWorkflow(flow, input, handlers, signal) {
  validateWorkflow(flow);
  const context = { input, steps: {} };
  let result = null;
  for (const raw of flow.steps) {
    signal.throwIfAborted();
    const step = interpolate(raw, context);
    const child = AbortSignal.any([signal, AbortSignal.timeout((step.timeoutSeconds || 120) * 1000)]);
    result = await handlers.execute(step, child);
    // Cancellation after dispatch must not turn a committed business result into a retry.
    context.steps[raw.id] = result;
  }
  return result;
}
export const starterWorkflow = {
  name: '创建任务草稿', version: 1, enabled: true,
  inputExample: { name: '晨曦', altitude: 50 },
  steps: [
    { id: 'create', type: 'tool', tool: 'mcp__groundstation__create_task', args: { task: { task_type: '放飞前出任务', task_name: '{{input.name}}', parameters: { mode: '原地起飞', takeoff_altitude: '{{input.altitude}}' } } } },
    { id: 'result', type: 'output', value: '已创建任务 {{steps.create.task_id}}，状态：{{steps.create.status}}。' },
  ],
};
