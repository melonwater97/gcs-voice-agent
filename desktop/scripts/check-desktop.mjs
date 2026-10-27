import { _electron } from '@playwright/test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const executablePath = path.join(root, 'release', 'win-unpacked', '领航语音助手.exe');
const env = { ...process.env, LH_NO_ERROR_DIALOG: '1' }; delete env.ELECTRON_RUN_AS_NODE; delete env.LH_UI_URL;
const app = await _electron.launch({ executablePath, env, timeout: 60000 });
try {
  const page = await app.firstWindow({ timeout: 60000 });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.getByRole('button', { name: '设置', exact: true }).waitFor();
  const state = await page.evaluate(() => window.linghang.request('/api/state', 'GET'));
  assert.equal(state.engine, 'Pi 1.4.0');
  assert.equal(state.config.model.model, 'deepseek-flash');
  const configured = state.config.mcp.find(service => service.name === 'python_tasks');
  assert.equal(configured?.enabled, true);
  assert.equal(configured.env.LH_PROJECT_ROOT.includes('resources'), true);
  const model = await page.evaluate(() => window.linghang.request('/api/diagnostics', 'POST', { type: 'model' }));
  assert.equal(model.reachable, true);
  const tools = await page.evaluate(() => window.linghang.request('/api/diagnostics', 'POST', { type: 'mcp', id: 'python-task-simulator' }));
  assert.deepEqual(tools.tools.map(t => t.name).sort(), ['create_task', 'execute_task', 'upload_task']);
  const speech = await page.evaluate(() => window.linghang.request('/api/diagnostics', 'POST', { type: 'voice' }));
  assert.equal(speech.modelFilesPresent, true); assert.equal(speech.dependencies.funasr, true);
  await page.getByRole('button', { name: '轨迹', exact: true }).click();
  await page.getByRole('region', { name: '轨迹视图' }).waitFor();
  await page.screenshot({ path: path.join(root, '.data', 'desktop-preview.png'), fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ startup: 'ok', model: state.config.model.model, modelReachable: true,
    mcpTools: tools.tools.map(t => t.name), asrFiles: speech.modelFilesPresent, pythonDependencies: speech.dependencies,
    inputDevices: speech.devices.length, pageErrors: errors }, null, 2));
} finally { await app.close(); }
