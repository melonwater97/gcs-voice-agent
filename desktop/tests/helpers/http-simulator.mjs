import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export async function httpSimulator(t, dir) {
  const root = fileURLToPath(new URL('../../..', import.meta.url));
  const source = "import sys, socket, uvicorn; from pathlib import Path; from simulator.http_server import GroundStationSimulator, create_app; sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128); app = create_app(GroundStationSimulator(Path(sys.argv[1]))); server = uvicorn.Server(uvicorn.Config(app, log_level='warning')); print(sock.getsockname()[1], flush=True); server.run(sockets=[sock])";
  const child = spawn(process.env.LH_TEST_PYTHON, ['-u', '-c', source, path.join(dir, 'http-state.json')], { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, PYTHONUTF8: '1' } });
  let errors = ''; child.stderr.on('data', data => { errors = (errors + data.toString()).slice(-2000); });
  t.after(async () => { if (child.exitCode === null) { const exited = new Promise(resolve => child.once('exit', resolve)); child.kill(); await exited; } });
  const port = await new Promise((resolve, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(new Error('HTTP simulator did not start')), 10000);
    child.stdout.on('data', data => { text += data; if (text.includes('\n')) { clearTimeout(timer); resolve(Number(text.trim())); } });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Simulator exited: ${code}: ${errors}`)); });
  });
  return `http://127.0.0.1:${port}`;
}
