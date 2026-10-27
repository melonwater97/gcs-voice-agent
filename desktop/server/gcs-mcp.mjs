import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { GroundStation, groundTools } from './groundstation.mjs';

const service = new GroundStation(JSON.parse(process.env.LH_GCS_CONFIG || '{}'), process.env.LH_REQUEST_DIR);
const server = new Server({ name: 'linghang-groundstation', version: '0.1.0' }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: groundTools }));
server.setRequestHandler(CallToolRequestSchema, async (request, extra) => {
  try {
    const result = await service.call(request.params.name, request.params.arguments, { signal: extra.signal });
    return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result };
  } catch (error) {
    return { isError: true, content: [{ type: 'text', text: error.message }], structuredContent: { error: error.message, unknown: Boolean(error.unknown) } };
  }
});
await server.connect(new StdioServerTransport());
