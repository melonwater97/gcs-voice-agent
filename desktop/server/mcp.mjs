import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

export async function connectMcp(config, { onClose } = {}) {
  const client = new Client({ name: 'linghang-assistant', version: '0.1.0' });
  client.onclose = () => onClose?.();
  const transport = config.transport === 'http'
    ? new StreamableHTTPClientTransport(new URL(config.url), { requestInit: { headers: config.headers || {} } })
    : new StdioClientTransport({ command: config.command, args: config.args || [], cwd: config.cwd || undefined, env: { ...process.env, ...(config.env || {}) }, stderr: 'pipe' });
  let stderr = '';
  if (transport.stderr) transport.stderr.on('data', d => { stderr = (stderr + d.toString()).slice(-1500); });
  try {
    await client.connect(transport, { timeout: 15000 });
    const listTools = async () => {
      const tools = []; let cursor;
      do {
        const page = await client.listTools(cursor ? { cursor } : undefined, { timeout: 15000 });
        tools.push(...page.tools); cursor = page.nextCursor;
        if (tools.length > 2000) throw new Error('MCP 工具列表超过初版上限');
      } while (cursor);
      return tools;
    };
    return { client, tools: await listTools(), listTools, pid: transport.pid || null, close: () => client.close() };
  } catch (error) { await client.close().catch(() => {}); throw new Error(`MCP 连接失败：${error.message}${stderr ? '\n' + stderr : ''}`); }
}
