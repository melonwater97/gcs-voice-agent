import path from 'node:path';
import Ajv from 'ajv';
import { connectMcp } from './mcp.mjs';
import { atomicJSON, readJSON } from './store.mjs';
import { managedMcpId, probeMcpHttp } from './runtime-config.mjs';

const ajv = new Ajv({ strict: false, allErrors: true });
const fingerprint = ({ enabled: _enabled, ...connection }) => JSON.stringify(connection);

export class McpRuntime {
  constructor(store, notify = () => {}, { connect = connectMcp } = {}) {
    this.store = store; this.notify = notify; this.connect = connect; this.entries = new Map(); this.closed = false;
    this.catalogFile = path.join(store.dir, 'mcp-catalog.json');
    this.catalog = readJSON(this.catalogFile, {});
  }
  config(id) {
    const config = this.store.config.mcp.find(service => service.id === id);
    if (!config) throw new Error('MCP 服务不存在');
    return config;
  }
  entry(id) {
    if (!this.entries.has(id)) this.entries.set(id, { id, status: 'paused', tools: this.catalog[id] || [], activeCalls: 0, connection: null });
    return this.entries.get(id);
  }
  changed() { if (!this.closed) this.notify({ type: 'mcp_runtime' }); }
  remember(entry, tools) {
    entry.tools = tools;
    this.catalog[entry.id] = this.store.safe(tools);
    atomicJSON(this.catalogFile, this.catalog);
  }
  snapshot(id) {
    const entry = this.entry(id);
    return this.store.safe({ id, status: entry.status, tools: entry.tools, toolCount: entry.tools.length,
      pid: entry.connection?.pid || null, activeCalls: entry.activeCalls, error: entry.error || '',
      startedAt: entry.startedAt, lastTestAt: entry.lastTestAt, testError: entry.testError || '', http: entry.http || null });
  }
  snapshots() { return this.store.config.mcp.map(service => this.snapshot(service.id)); }
  persistEnabled(id, enabled) {
    const config = structuredClone(this.store.config);
    config.mcp.find(service => service.id === id).enabled = enabled;
    this.store.saveConfig(config);
  }
  async start(id, { persist = true } = {}) {
    if (this.closed) throw new Error('MCP 管理器已关闭');
    this.config(id);
    const entry = this.entry(id);
    if (entry.status === 'pausing' || entry.stopping) throw new Error('服务正在暂停，请等待当前调用结束');
    if (persist) this.persistEnabled(id, true);
    if (entry.starting) return entry.starting;
    if (entry.connection) return this.snapshot(id);
    const config = structuredClone(this.config(id));
    entry.status = 'starting'; entry.error = ''; entry.http = null; this.changed();
    entry.starting = (async () => {
      let connection;
      try {
        connection = await this.connect(config, { onClose: () => {
          if (entry.connection !== connection) return;
          entry.connection = null;
          if (entry.status !== 'pausing' && !this.closed) { entry.status = 'failed'; entry.error = 'MCP 进程已退出或连接已断开'; this.changed(); }
        } });
        if (this.closed || !this.store.config.mcp.find(service => service.id === id)?.enabled) {
          await connection.close(); entry.status = 'paused'; return this.snapshot(id);
        }
        entry.connection = connection; entry.signature = fingerprint(config);
        this.remember(entry, connection.tools);
        entry.status = 'running'; entry.startedAt = Date.now();
        return this.snapshot(id);
      } catch (error) {
        if (connection && entry.connection === connection) { entry.connection = null; await connection.close().catch(() => {}); }
        entry.status = this.config(id).enabled && !this.closed ? 'failed' : 'paused'; entry.error = this.store.safe(error.message);
        throw error;
      } finally { entry.starting = null; this.changed(); }
    })();
    return entry.starting;
  }
  async stopEntry(entry) {
    if (entry.stopping) return entry.stopping;
    entry.stopping = (async () => {
      const connection = entry.connection; entry.connection = null;
      try { if (connection) await connection.close(); }
      finally { entry.status = 'paused'; entry.startedAt = undefined; entry.error = ''; this.changed(); }
    })();
    try { await entry.stopping; } finally { entry.stopping = null; }
  }
  async pause(id, { persist = true } = {}) {
    this.config(id);
    if (persist) this.persistEnabled(id, false);
    const entry = this.entry(id);
    entry.status = 'pausing'; this.changed();
    if (entry.starting) await entry.starting.catch(() => {});
    if (!entry.activeCalls) await this.stopEntry(entry);
    return this.snapshot(id);
  }
  async sync() {
    const configured = new Set(this.store.config.mcp.map(service => service.id));
    for (const [id, entry] of this.entries) if (!configured.has(id)) {
      await this.stopEntry(entry); this.entries.delete(id); delete this.catalog[id];
    }
    const results = await Promise.allSettled(this.store.config.mcp.map(async service => {
      const entry = this.entry(service.id);
      if (entry.connection && entry.signature !== fingerprint(service)) await this.stopEntry(entry);
      if (!service.enabled) {
        if (entry.connection || entry.starting) await this.pause(service.id, { persist: false });
      } else if (entry.starting) await entry.starting;
      else if (entry.status !== 'failed' && entry.status !== 'pausing') await this.start(service.id, { persist: false });
    }));
    // Failures remain visible per service; one unavailable MCP must not block the UI.
    return results;
  }
  async restart(id) {
    const entry = this.entry(id);
    if (entry.activeCalls) throw new Error('请等待当前工具调用结束后修改配置');
    if (entry.starting) await entry.starting.catch(() => {});
    await this.stopEntry(entry); entry.http = null;
    if (this.config(id).enabled) return this.start(id, { persist: false });
    return this.snapshot(id);
  }
  async test(id) {
    const config = this.config(id), entry = this.entry(id);
    if (entry.testing) return entry.testing;
    entry.testing = (async () => {
      let temporary;
      try {
        if (entry.starting) await entry.starting;
        if (entry.status === 'pausing') throw new Error('服务正在暂停，请稍后测试');
        const connection = entry.connection || (temporary = await this.connect(config));
        const tools = temporary ? connection.tools : await connection.listTools();
        this.remember(entry, tools); entry.testError = ''; entry.lastTestAt = Date.now();
        entry.http = id === managedMcpId ? await probeMcpHttp(this.store, id) : null;
        return { connected: true, tools, http: entry.http };
      } catch (error) { entry.testError = this.store.safe(error.message); entry.lastTestAt = Date.now(); throw error; }
      finally { if (temporary) await temporary.close(); entry.testing = null; this.changed(); }
    })();
    return entry.testing;
  }
  async call(id, name, args, { signal, timeout = 120000, onProgress } = {}) {
    const config = this.config(id), entry = this.entry(id);
    if (!config.enabled || entry.status !== 'running' || !entry.connection) throw new Error('MCP 服务已暂停或未连接，请先启动服务');
    const tool = entry.tools.find(item => item.name === name);
    if (!tool) throw new Error('MCP 工具不存在');
    const validate = ajv.compile(tool.inputSchema);
    if (!validate(args)) throw new Error('工具参数校验失败：' + ajv.errorsText(validate.errors));
    signal?.throwIfAborted(); entry.activeCalls++; this.changed();
    try {
      return await entry.connection.client.callTool({ name, arguments: args }, undefined, { signal, timeout,
        ...(onProgress ? { onprogress: onProgress } : {}) });
    } finally {
      entry.activeCalls--;
      if (entry.status === 'pausing' && !entry.activeCalls) await this.stopEntry(entry);
      this.changed();
    }
  }
  async piTools() {
    await this.sync();
    return this.store.config.mcp.filter(service => service.enabled && this.entry(service.id).status === 'running').flatMap(service =>
      this.entry(service.id).tools.map(tool => ({
        name: `mcp__${service.name}__${tool.name}`, label: `${service.name}/${tool.name}`,
        description: tool.description || tool.title || tool.name,
        parameters: { ...tool.inputSchema, type: tool.inputSchema.type || 'object', properties: tool.inputSchema.properties || {} },
        execute: async (_callId, args, signal, onUpdate) => {
          const result = await this.call(service.id, tool.name, args, { signal, onProgress: progress => onUpdate?.({ content: [{ type: 'text', text: progress.message || `进度 ${progress.progress}` }], details: { server: service.name, tool: tool.name } }) });
          if (result.isError) throw new Error(result.content?.map(item => item.text || '').join('\n') || 'MCP 工具调用失败');
          return { content: (result.content || []).map(item => ['text', 'image'].includes(item.type) ? item : { type: 'text', text: JSON.stringify(item) }), details: result };
        },
      })));
  }
  async close() {
    this.closed = true;
    for (const entry of this.entries.values()) {
      if (entry.starting) await entry.starting.catch(() => {});
      if (entry.testing) await entry.testing.catch(() => {});
      await this.stopEntry(entry);
    }
  }
}
