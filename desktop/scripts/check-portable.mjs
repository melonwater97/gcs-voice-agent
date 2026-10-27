import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const binary = path.join(root, 'release', 'Linghang-Assistant-Portable-0.1.0-x64.exe');
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
const env = { ...process.env, LH_NO_ERROR_DIALOG: '1' }; delete env.ELECTRON_RUN_AS_NODE; delete env.LH_UI_URL;
const child = spawn(binary, [`--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1'], { env, stdio: 'pipe', windowsHide: true });
child.stdout.resume(); child.stderr.resume();
let exitCode, launchError;
child.on('error', error => { launchError = error; });
const exited = new Promise(resolve => child.once('exit', code => { exitCode = code; resolve(code); }));
let browser, page, debuggerURL;
try {
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (launchError) throw launchError;
    if (exitCode !== undefined) throw new Error(`免安装启动器提前退出：${exitCode}`);
    try { const response = await fetch(`http://127.0.0.1:${port}/json/version`); if (response.ok) { debuggerURL = (await response.json()).webSocketDebuggerUrl; break; } } catch { /* The launcher is still extracting the application. */ }
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  assert.ok(debuggerURL, '免安装程序没有就绪的调试端点');
  browser = await chromium.connectOverCDP(debuggerURL, { timeout: 10000 });
  const until = Date.now() + 30000;
  while (Date.now() < until) {
    page = browser.contexts().flatMap(c => c.pages()).find(p => p.url().includes('/dist/index.html'));
    if (page) break;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  assert.ok(page, '未找到免安装版主界面');
  await page.getByRole('button', { name: '设置', exact: true }).waitFor();
  const state = await page.evaluate(() => window.linghang.request('/api/state', 'GET'));
  assert.equal(state.config.model.model, 'deepseek-flash');
  const model = await page.evaluate(() => window.linghang.request('/api/diagnostics', 'POST', { type: 'model' }));
  assert.equal(model.reachable, true);
  const tools = await page.evaluate(() => window.linghang.request('/api/diagnostics', 'POST', { type: 'mcp', id: 'python-task-simulator' }));
  assert.equal(tools.tools.length, 3);
  const speech = await page.evaluate(() => window.linghang.request('/api/diagnostics', 'POST', { type: 'voice' }));
  assert.equal(speech.modelFilesPresent, true);
  await page.getByRole('button', { name: '轨迹', exact: true }).click();
  await page.getByRole('region', { name: '轨迹视图' }).waitFor();
  await page.screenshot({ path: path.join(root, '.data', 'portable-preview.png'), fullPage: true });
  console.log(JSON.stringify({ portable: 'ok', model: state.config.model.model, mcpTools: tools.tools.map(t => t.name), asrFiles: speech.modelFilesPresent }, null, 2));
} finally {
  if (page && !page.isClosed()) await page.evaluate(() => window.linghang.window('close')).catch(() => {});
  if (browser) await browser.close().catch(() => {});
  await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 12000))]);
  if (exitCode === undefined) child.kill();
}
