const { app, BrowserWindow, ipcMain, dialog, safeStorage, screen } = require('electron');
const { fork } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { randomBytes } = require('node:crypto');
const { migrateWebData } = require('./data-migration.cjs');
let win, backend, backendURL, quitting = false, eventController;
const token = randomBytes(32).toString('hex');

function owned(event) { if (!win || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame) throw new Error('不支持的调用来源'); }
async function backendRequest(route, method = 'GET', body) {
  const response = await fetch(backendURL + route, { method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  const value = await response.json(); if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`); return value;
}
async function watchEvents() {
  while (!quitting) {
    try {
      eventController = new AbortController();
      const response = await fetch(backendURL + '/api/events', { headers: { Authorization: `Bearer ${token}` }, signal: eventController.signal });
      if (!response.ok) throw new Error('事件通道连接失败');
      const decoder = new TextDecoder(); let buffer = '';
      for await (const chunk of response.body) {
        buffer += decoder.decode(chunk, { stream: true });
        let end;
        while ((end = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, end); buffer = buffer.slice(end + 2);
          if (block.startsWith('data: ') && win && !win.isDestroyed()) win.webContents.send('backend-event', JSON.parse(block.slice(6)));
        }
      }
    } catch (error) { if (!quitting && win && !win.isDestroyed()) win.webContents.send('backend-event', { type: 'disconnected', message: error.message }); }
    if (!quitting) { await new Promise(resolve => setTimeout(resolve, 1000)); if (win && !win.isDestroyed()) win.webContents.send('backend-event', { type: 'reconnected' }); }
  }
}
async function createWindow() {
  const dataDir = process.env.LH_DATA_DIR || path.join(app.getPath('userData'), 'data'); fs.mkdirSync(dataDir, { recursive: true });
  const keyFile = path.join(dataDir, 'system.key');
  if (!safeStorage.isEncryptionAvailable()) throw new Error('系统凭据保护服务不可用，无法打开加密配置');
  const key = fs.existsSync(keyFile) ? safeStorage.decryptString(fs.readFileSync(keyFile)) : randomBytes(32).toString('base64');
  if (!fs.existsSync(keyFile)) fs.writeFileSync(keyFile, safeStorage.encryptString(key));
  // app.getAppPath() is resources/app.asar in a packaged build. Windows needs
  // an actual directory for cwd, even though Electron can load scripts from ASAR.
  const runtimeDir = app.isPackaged ? path.dirname(process.execPath) : app.getAppPath();
  const importDir = process.env.LH_IMPORT_DIR || (app.isPackaged ? path.resolve(runtimeDir, '..', '..', '.data') : path.join(app.getAppPath(), '.data'));
  migrateWebData(importDir, dataDir, key);
  backend = fork(path.join(app.getAppPath(), 'server', 'index.mjs'), [], { cwd: runtimeDir, windowsHide: true, execArgv: [],
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'], env: { ...process.env, ELECTRON_RUN_AS_NODE: '1', LH_NODE_EXEC: process.execPath, DESKTOP_PORT: '0', LH_DATA_DIR: dataDir,
      LH_CREDENTIAL_KEY: key, LH_BACKEND_TOKEN: token, LH_SPEECH_ROOT: app.isPackaged ? path.join(process.resourcesPath, 'speech') : path.resolve(app.getAppPath(), '..'),
      LH_VOICE_WORKER_PATH: app.isPackaged ? path.join(process.resourcesPath, 'speech', 'python', 'voice_worker.py') : path.join(app.getAppPath(), 'python', 'voice_worker.py') } });
  let logs = '';
  backend.stdout.on('data', data => { logs = (logs + data.toString()).slice(-2000); });
  backend.stderr.on('data', data => { logs = (logs + data.toString()).slice(-2000); });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('后台启动超时')), 30000);
    backend.once('error', error => { clearTimeout(timer); reject(error); });
    backend.once('exit', code => { clearTimeout(timer); reject(new Error(`后台进程退出 ${code}：${logs}`)); });
    backend.once('message', message => { clearTimeout(timer); backendURL = `http://127.0.0.1:${message.port}`; resolve(); });
  });
  const boundsFile = path.join(dataDir, 'window.json'); let bounds = { width: 960, height: 820 };
  try { bounds = { ...bounds, ...JSON.parse(fs.readFileSync(boundsFile, 'utf8')) }; } catch (error) { if (error.code !== 'ENOENT') console.warn('窗口位置将使用默认值'); }
  if (Number.isFinite(bounds.x) && !screen.getAllDisplays().some(d => bounds.x >= d.workArea.x && bounds.x < d.workArea.x + d.workArea.width && bounds.y >= d.workArea.y && bounds.y < d.workArea.y + d.workArea.height)) { delete bounds.x; delete bounds.y; }
  win = new BrowserWindow({ ...bounds, minWidth: 480, minHeight: 620, frame: false, title: '领航语音助手', backgroundColor: '#fafafd', show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false } });
  const config = await backendRequest('/api/state'); win.setAlwaysOnTop(config.config.general.alwaysOnTop);
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', event => event.preventDefault());
  win.on('close', () => { if (!win.isMaximized()) fs.writeFileSync(boundsFile, JSON.stringify(win.getBounds())); });
  win.once('ready-to-show', () => win.show());
  if (process.env.LH_UI_URL) await win.loadURL(process.env.LH_UI_URL); else await win.loadFile(path.join(app.getAppPath(), 'dist', 'index.html'));
  void watchEvents();
}
ipcMain.handle('backend-request', async (event, route, method, body) => {
  owned(event);
  if (!/^\/api\/[a-zA-Z0-9_/-]+$/.test(route) || route === '/api/events' || !['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) throw new Error('无效的应用请求');
  const result = await backendRequest(route, method, body);
  if (route === '/api/settings' && method === 'PUT') win.setAlwaysOnTop(Boolean(result.general.alwaysOnTop));
  return result;
});
ipcMain.handle('pick-directory', async event => { owned(event); const result = await dialog.showOpenDialog(win, { properties: ['openDirectory'] }); return result.canceled ? null : result.filePaths[0]; });
ipcMain.on('window-command', (event, command) => { owned(event); if (command === 'minimize') win.minimize(); if (command === 'maximize') win.isMaximized() ? win.unmaximize() : win.maximize(); if (command === 'close') win.close(); });
app.whenReady().then(createWindow).catch(error => {
  const message = '领航启动失败：' + error.message;
  console.error(message);
  try { fs.appendFileSync(path.join(app.getPath('userData'), 'startup.log'), new Date().toISOString() + ' ' + message + '\n'); } catch { /* Reporting must not hide the original startup error. */ }
  if (!process.env.LH_NO_ERROR_DIALOG) dialog.showErrorBox('领航启动失败', error.message);
  app.quit();
});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (quitting || !backend) return;
  event.preventDefault(); quitting = true; eventController?.abort();
  if (backend.connected) backend.send({ type: 'shutdown' });
  const timer = setTimeout(() => { backend.kill(); app.quit(); }, 5000);
  backend.once('exit', () => { clearTimeout(timer); app.quit(); });
});
