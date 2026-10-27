import path from 'node:path';
import { fileURLToPath } from 'node:url';

export function prepareRuntimeConfig(store, { speechRoot = process.env.LH_SPEECH_ROOT || fileURLToPath(new URL('../..', import.meta.url)), node = process.env.LH_NODE_EXEC || process.execPath } = {}) {
  const config = structuredClone(store.config);
  let changed = false;
  for (const service of config.mcp) {
    if (service.id !== 'python-task-simulator' || !service.args?.some(arg => /(?:^|[\\/])python-mcp\.mjs$/.test(arg))) continue;
    const next = { ...service, command: node, args: [fileURLToPath(new URL('./python-mcp.mjs', import.meta.url))], cwd: speechRoot,
      env: { ...service.env, ELECTRON_RUN_AS_NODE: '1', LH_PROJECT_ROOT: speechRoot,
        TASK_OUTPUT_DIR: path.join(store.dir, 'python-tasks', 'output'), TASK_STATE_PATH: path.join(store.dir, 'python-tasks', 'state.json') } };
    if (JSON.stringify(next) !== JSON.stringify(service)) { Object.assign(service, next); changed = true; }
  }
  if (changed) store.saveConfig(config);
}
