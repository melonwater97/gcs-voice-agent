import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('..', import.meta.url));
const web = !process.argv.includes('--electron');
let backend;
if (web) { const { startServer } = await import('../server/index.mjs'); backend = await startServer(); }
const vite = await createServer({ root, server: { host: '127.0.0.1', port: 5180, strictPort: true } }); await vite.listen();
console.log('领航界面：http://127.0.0.1:5180');
let electron;
if (!web) { const require = createRequire(import.meta.url); electron = spawn(require('electron'), [root], { windowsHide: true, stdio: 'inherit', env: { ...process.env, LH_UI_URL: 'http://127.0.0.1:5180', ELECTRON_RUN_AS_NODE: '' } }); electron.on('exit', () => { void close(); }); }
let closing = false;
async function close() { if (closing) return; closing = true; if (electron && electron.exitCode === null) electron.kill(); await vite.close(); if (backend) await backend.close(); process.exit(0); }
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { void close(); });
