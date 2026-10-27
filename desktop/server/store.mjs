import fs from 'node:fs';
import path from 'node:path';
import { randomUUID, randomBytes, createCipheriv, createDecipheriv } from 'node:crypto';

export const uid = () => randomUUID();
export const MASK = '••••••••';
export const defaults = () => ({
  model: { baseUrl: 'http://127.0.0.1:18000/v1', model: 'Ornith-1.5-35B-A3B-MLX-6bit', apiKey: '', contextWindow: 32768, maxTokens: 4096 },
  voice: { python: 'python', modelDir: '', hotwordsFile: '', device: '', maxSeconds: 90 },
  mcp: [], skills: [], workflows: [], general: { alwaysOnTop: false },
});
export function atomicJSON(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.${uid()}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2), { mode: 0o600 });
  fs.renameSync(temp, file);
}
export function readJSON(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return fallback; throw new Error(`无法读取数据 ${path.basename(file)}：${error.message}`); }
}
const secretKey = /api.?key|(?:^|[_-])(?:access[_-]?|refresh[_-]?)?token$|(?:access|refresh)Token$|password|secret|authorization|cookie|credential/i;
export function redact(value, knownSecrets = []) {
  if (typeof value === 'string') return knownSecrets.filter(s => s && s.length >= 4).reduce((text, s) => text.split(s).join(MASK), value);
  if (Array.isArray(value)) return value.map(v => redact(v, knownSecrets));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, secretKey.test(k) && v ? MASK : redact(v, knownSecrets)]));
  return value;
}
export function mergeMasked(incoming, previous) {
  if (incoming === MASK) return previous ?? '';
  if (Array.isArray(incoming)) return incoming.map(item => mergeMasked(item, previous?.find?.(old => old.id === item.id)));
  if (incoming && typeof incoming === 'object') return Object.fromEntries(Object.entries(incoming).map(([k, v]) => [k, mergeMasked(v, previous?.[k])]));
  return incoming;
}
export function validateSettings(config) {
  if (!config?.model || !config?.voice || !config?.general) throw new Error('配置结构不完整');
  delete config.gcs;
  for (const [label, raw] of [['模型', config.model.baseUrl]]) {
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error(`${label}地址必须为 HTTP(S)，凭据请单独填写`);
  }
  if (!config.model.model?.trim()) throw new Error('请填写模型名称');
  if (!Number.isInteger(config.voice.maxSeconds) || config.voice.maxSeconds < 1 || config.voice.maxSeconds > 600) throw new Error('录音上限应为 1–600 秒');
  if (!Number.isInteger(config.model.maxTokens) || config.model.maxTokens < 128 || config.model.maxTokens > 32768) throw new Error('模型输出上限应为 128–32768');
  if (!Number.isInteger(config.model.contextWindow) || config.model.contextWindow < config.model.maxTokens) throw new Error('上下文窗口应不小于输出上限');
  if (config.voice.device !== '' && (!Number.isInteger(Number(config.voice.device)) || Number(config.voice.device) < 0)) throw new Error('麦克风设备编号无效');
  for (const field of ['mcp', 'skills', 'workflows']) {
    if (!Array.isArray(config[field])) throw new Error(`${field} 应为列表`);
    const ids = config[field].map(v => v.id);
    if (ids.some(id => typeof id !== 'string' || !id) || new Set(ids).size !== ids.length) throw new Error(`${field} 编号必须唯一`);
  }
  for (const item of config.mcp) {
    if (!/^[a-zA-Z0-9_]{1,32}$/.test(item.name)) throw new Error('MCP 名称使用字母、数字、下划线');
    if (!['stdio', 'http'].includes(item.transport)) throw new Error('未知 MCP 传输类型');
    if (item.transport === 'stdio' && (!item.command || !Array.isArray(item.args))) throw new Error('stdio MCP 需要 command 和 args 数组');
    if (item.transport === 'http' && !/^https?:\/\//.test(item.url || '')) throw new Error('HTTP MCP 需要有效地址');
  }
  if (new Set(config.mcp.map(v => v.name)).size !== config.mcp.length) throw new Error('MCP 服务名称重复');
  return config;
}

export class Store {
  constructor(dir, encryptionKey) {
    this.dir = dir;
    fs.mkdirSync(dir, { recursive: true });
    const keyPath = path.join(dir, 'development.key');
    if (!encryptionKey && !fs.existsSync(keyPath)) fs.writeFileSync(keyPath, randomBytes(32), { mode: 0o600 });
    this.key = encryptionKey ? Buffer.from(encryptionKey, 'base64') : fs.readFileSync(keyPath);
    const stored = readJSON(path.join(dir, 'settings.enc.json'), null);
    const defaultsValue = defaults();
    if (stored) {
      const saved = this.decrypt(stored);
      this.config = { ...defaultsValue, ...saved,
        model: { ...defaultsValue.model, ...saved.model },
        voice: { ...defaultsValue.voice, ...saved.voice },
        general: { ...defaultsValue.general, ...saved.general },
        mcp: saved.mcp || [], skills: saved.skills || [], workflows: saved.workflows || [],
      };
    } else this.config = defaultsValue;
    for (const name of ['sessions', 'knowledge', 'requests', 'pi']) fs.mkdirSync(path.join(dir, name), { recursive: true });
    // A restarted host restores records, never replays unfinished external operations.
    for (const item of this.listSessions()) {
      const session = this.getSession(item.id);
      let changed = false;
      for (const row of session.records) if (['running', 'streaming'].includes(row.status)) {
        row.status = row.kind === 'tool' ? 'unknown' : 'interrupted'; changed = true;
      }
      if (changed) this.saveSession(session);
    }
  }
  encrypt(value) {
    const iv = randomBytes(12), cipher = createCipheriv('aes-256-gcm', this.key, iv);
    const data = Buffer.concat([cipher.update(JSON.stringify(value), 'utf8'), cipher.final()]);
    return { iv: iv.toString('base64'), data: data.toString('base64'), tag: cipher.getAuthTag().toString('base64') };
  }
  decrypt(value) {
    const cipher = createDecipheriv('aes-256-gcm', this.key, Buffer.from(value.iv, 'base64'));
    cipher.setAuthTag(Buffer.from(value.tag, 'base64'));
    return JSON.parse(Buffer.concat([cipher.update(Buffer.from(value.data, 'base64')), cipher.final()]).toString('utf8'));
  }
  publicConfig() {
    const c = structuredClone(this.config);
    if (c.model.apiKey) c.model.apiKey = MASK;
    delete c.gcs;
    for (const server of c.mcp) for (const field of ['env', 'headers']) if (server[field]) server[field] = Object.fromEntries(Object.entries(server[field]).map(([k, v]) => [k, secretKey.test(k) && v ? MASK : v]));
    return c;
  }
  saveConfig(incoming) {
    const next = validateSettings(mergeMasked(incoming, this.config));
    atomicJSON(path.join(this.dir, 'settings.enc.json'), this.encrypt(next));
    this.config = next;
    return this.publicConfig();
  }
  secrets() { return [this.config.model.apiKey, this.config.gcs?.token, ...this.config.mcp.flatMap(s => [...Object.entries(s.env || {}), ...Object.entries(s.headers || {})].filter(([key]) => secretKey.test(key)).map(([, value]) => value))]; }
  safe(value) { return redact(value, this.secrets()); }
  sessionPath(id) {
    if (!/^[0-9a-f-]{36}$/.test(id)) throw new Error('会话编号无效');
    return path.join(this.dir, 'sessions', `${id}.json`);
  }
  newSession() { const s = { id: uid(), title: '新对话', createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), records: [] }; this.saveSession(s); return s; }
  getSession(id) { const s = readJSON(this.sessionPath(id), null); if (!s) throw new Error('会话不存在'); return s; }
  saveSession(s) { s.updatedAt = new Date().toISOString(); atomicJSON(this.sessionPath(s.id), s); }
  listSessions() {
    return fs.readdirSync(path.join(this.dir, 'sessions'), { withFileTypes: true }).filter(e => e.isFile() && e.name.endsWith('.json')).map(e => {
      const s = readJSON(path.join(this.dir, 'sessions', e.name), null);
      return { id: s.id, title: s.title, updatedAt: s.updatedAt, count: s.records.length };
    }).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }
}
