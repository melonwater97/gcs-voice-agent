import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import YAML from 'yaml';
import { uid } from './store.mjs';

export const managedMcpId = 'python-task-simulator';
export const managedConfigPath = store => path.join(store.dir, 'mcp', 'groundstation.yaml');
const run = promisify(execFile);

function requireManaged(store, id) {
  const service = store.config.mcp.find(item => item.id === id);
  if (id !== managedMcpId || !service) throw new Error('该 MCP 服务没有内置 YAML 配置入口');
  return service;
}
export function readMcpYaml(store, id) {
  requireManaged(store, id);
  return { yaml: fs.readFileSync(managedConfigPath(store), 'utf8'), file: managedConfigPath(store) };
}
export async function saveMcpYaml(store, id, text) {
  const service = requireManaged(store, id);
  if (typeof text !== 'string' || Buffer.byteLength(text) > 65536) throw new Error('YAML 配置最大 64 KiB');
  const file = managedConfigPath(store), temporary = `${file}.${uid()}.tmp`;
  fs.writeFileSync(temporary, text, { mode: 0o600 });
  try {
    await run(service.command, ['-m', 'integrations.mcp_server', '--check-config', '--config', temporary], {
      cwd: service.cwd, windowsHide: true, timeout: 15000, maxBuffer: 65536,
      env: { ...process.env, ...service.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }, encoding: 'utf8',
    }).catch(error => { throw new Error(error.stderr?.trim() || '无法检查 YAML 配置，请检查 MCP 的 Python 环境'); });
    fs.renameSync(temporary, file);
  } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
  return readMcpYaml(store, id);
}
export async function probeMcpHttp(store, id) {
  const service = requireManaged(store, id);
  const { stdout } = await run(service.command, ['-m', 'integrations.mcp_server', '--probe-config', '--config', managedConfigPath(store)], {
    cwd: service.cwd, windowsHide: true, timeout: 12000, maxBuffer: 65536, encoding: 'utf8',
    env: { ...process.env, ...service.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
  });
  return JSON.parse(stdout);
}

export function prepareRuntimeConfig(store, { speechRoot = process.env.LH_SPEECH_ROOT || fileURLToPath(new URL('../..', import.meta.url)) } = {}) {
  const config = structuredClone(store.config);
  let changed = false;
  const oldGcs = config.gcs;
  if (oldGcs?.baseUrl && !config.mcp.some(service => service.id === managedMcpId)) {
    config.mcp.push({ id: managedMcpId, name: 'groundstation', transport: 'stdio', enabled: true, command: config.voice.python, args: [], env: {} });
  }
  for (const service of config.mcp) {
    if (service.id !== managedMcpId) continue;
    const file = managedConfigPath(store);
    if (!fs.existsSync(file)) {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      const defaults = { ground_station: { base_url: oldGcs?.baseUrl || 'http://127.0.0.1:18003', timeout_seconds: oldGcs?.timeoutSeconds || 30,
        simulation: !oldGcs?.baseUrl, token_env: 'LH_GCS_TOKEN', agent_id: 'AGENT-01', station_id: 'GS-01' } };
      fs.writeFileSync(file, '# 自建 Python MCP 服务配置；保存后下一次工具调用生效。\n' + YAML.stringify(defaults), { mode: 0o600 });
    }
    const legacy = service.args?.some(arg => /(?:^|[\\/])python-mcp\.mjs$/.test(arg));
    const env = { ...service.env };
    for (const key of ['LH_LEGACY_PYTHON', 'LH_PROJECT_ROOT', 'ELECTRON_RUN_AS_NODE']) delete env[key];
    if (oldGcs?.token && !env.LH_GCS_TOKEN) env.LH_GCS_TOKEN = oldGcs.token;
    for (const key of ['TASK_OUTPUT_DIR', 'TASK_STATE_PATH']) delete env[key];
    const next = { ...service, command: legacy ? service.env?.LH_LEGACY_PYTHON || config.voice.python || 'python' : service.command || config.voice.python || 'python',
      args: ['-m', 'integrations.mcp_server', '--config', file, '--request-dir', path.join(store.dir, 'requests', 'groundstation')], cwd: speechRoot,
      env: { ...env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } };
    if (JSON.stringify(next) !== JSON.stringify(service)) { Object.assign(service, next); changed = true; }
  }
  if ('gcs' in config) { delete config.gcs; changed = true; }
  for (const flow of config.workflows) for (const step of flow.steps || []) {
    const service = config.mcp.find(item => item.id === managedMcpId);
    if (service && step.tool?.startsWith('mcp__groundstation__')) {
      step.tool = step.tool.replace('mcp__groundstation__', `mcp__${service.name}__`); changed = true;
    }
  }
  if (changed) store.saveConfig(config);
}
