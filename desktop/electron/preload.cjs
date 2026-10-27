const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('linghang', {
  request: (path, method, body) => ipcRenderer.invoke('backend-request', path, method, body),
  subscribe: callback => { const handler = (_event, value) => callback(value); ipcRenderer.on('backend-event', handler); return () => ipcRenderer.removeListener('backend-event', handler); },
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  window: command => ipcRenderer.send('window-command', command),
});
