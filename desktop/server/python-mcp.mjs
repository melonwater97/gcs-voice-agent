// Adapt the existing Python MCP business envelopes to model-facing tool arguments.
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { randomUUID } from 'node:crypto';
import Ajv from 'ajv';
import { connectMcp } from './mcp.mjs';

const original = await connectMcp({ transport: 'stdio', command: process.env.LH_LEGACY_PYTHON || 'python',
  args: ['-m', 'integrations.mcp_server'], cwd: process.env.LH_PROJECT_ROOT,
  env: { PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', ...(process.env.TASK_OUTPUT_DIR ? { TASK_OUTPUT_DIR: process.env.TASK_OUTPUT_DIR } : {}), ...(process.env.TASK_STATE_PATH ? { TASK_STATE_PATH: process.env.TASK_STATE_PATH } : {}) } });
function inline(node, definitions, seen = []) {
  if (Array.isArray(node)) return node.map(value => inline(value, definitions, seen));
  if (!node || typeof node !== 'object') return node;
  if (node.$ref) {
    const name = node.$ref.split('/').at(-1);
    if (!seen.includes(name) && definitions[name]) return inline(definitions[name], definitions, [...seen, name]);
  }
  return Object.fromEntries(Object.entries(node).filter(([key]) => key !== '$defs').map(([key, value]) => [key, inline(value, definitions, seen)]));
}
const tools = original.tools.filter(t => ['create_task', 'upload_task', 'execute_task'].includes(t.name)).map(tool => {
  const schema = inline(tool.inputSchema, tool.inputSchema.$defs || {}), payload = schema.properties.payload;
  return { name: tool.name, description: `本地任务模拟工具，不连接真实地面站或飞控。${tool.description || ''}`,
    inputSchema: tool.name === 'create_task' ? { type: 'object', required: ['task'], additionalProperties: false, properties: { task: payload.properties.task } } : payload };
});
const ajv = new Ajv({ strict: false, allErrors: true });
const checks = new Map(tools.map(t => [t.name, ajv.compile(t.inputSchema)]));
const server = new Server({ name: 'python-task-simulator', version: '0.1.0' }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }));
server.setRequestHandler(CallToolRequestSchema, async (request, extra) => {
  try {
    const { name, arguments: args } = request.params, check = checks.get(name);
    if (!check || !check(args)) throw new Error('Python 工具参数无效：' + (check ? ajv.errorsText(check.errors) : '工具不存在'));
    const response = await original.client.callTool({ name, arguments: {
      header: { msg_id: `MSG-${randomUUID()}`, msg_type: name.toUpperCase(), version: '1.0.0', src: { type: 'BACKGROUND', id: 'AGENT-01' }, dst: { type: 'BACKGROUND', id: 'MCP-01' } },
      payload: name === 'create_task' ? { action: 'create_task', task: args.task } : args,
    } }, undefined, { signal: extra.signal, timeout: 30000 });
    if (response.isError) throw new Error(response.content.map(c => c.text || '').join('\n'));
    const message = response.structuredContent;
    if (message?.header?.src?.id !== 'MCP-01' || message.header.dst?.id !== 'AGENT-01' || message.header.version !== '1.0.0') throw new Error('原 Python MCP 回执路由或格式不匹配');
    if (message.header.msg_type === 'ERROR') throw new Error(message.payload.error || '原 Python MCP 拒绝操作');
    if (message.header.msg_type !== 'TASK_RESULT') throw new Error('原 Python MCP 返回了非任务回执');
    const data = { ...message.payload, simulation: true, source: 'python-task-simulator' };
    return { content: [{ type: 'text', text: JSON.stringify(data) }], structuredContent: data };
  } catch (error) { return { isError: true, content: [{ type: 'text', text: error.message }] }; }
});
await server.connect(new StdioServerTransport());
let closing = false;
const close = async () => { if (closing) return; closing = true; await original.close(); await server.close(); };
process.stdin.on('end', () => { void close(); });
process.on('SIGTERM', () => { void close(); });
