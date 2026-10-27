import fs from 'node:fs';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import YAML from 'yaml';

export function linuxPath(value) {
  if (process.platform !== 'win32') return value;
  const match = /^([A-Za-z]):[\\/](.*)$/.exec(value);
  if (!match) throw new Error('WSL 仿真要求本机盘符路径');
  return `/mnt/${match[1].toLowerCase()}/${match[2].replaceAll('\\', '/')}`;
}
export function setupFlightRuntime(store) {
  const root = fileURLToPath(new URL('../..', import.meta.url)), dir = path.join(store.dir, 'flights');
  fs.mkdirSync(dir, { recursive: true });
  const authority = path.join(dir, 'authority.key'), token = path.join(dir, 'bridge.token');
  if (!fs.existsSync(authority)) fs.writeFileSync(authority, randomBytes(32), { mode: 0o600 });
  if (!fs.existsSync(token)) fs.writeFileSync(token, randomBytes(32).toString('hex'), { mode: 0o600 });
  const config = path.join(dir, 'simulation.yaml');
  if (!fs.existsSync(config)) fs.writeFileSync(config, YAML.stringify({ simulation_only: true, target_system: 1, px4_root: '/opt/uav-sim/PX4-Autopilot',
    authority_key_file: linuxPath(authority), bridge_token_file: linuxPath(token), qgc_bridge_url: 'http://127.0.0.1:18320', data_dir: linuxPath(path.join(dir, 'backend')) }), { mode: 0o600 });
  const previous = store.config.mcp.find(service => service.id === 'px4-simulation');
  const service = { ...previous, id: 'px4-simulation', name: 'px4_sim', transport: 'stdio', enabled: previous?.enabled || false,
    command: process.platform === 'win32' ? 'wsl.exe' : path.join(root, 'uav/.venv-wsl/bin/python'),
    args: process.platform === 'win32' ? ['-d', 'Ubuntu-22.04', '--cd', linuxPath(root), '--exec', linuxPath(path.join(root, 'uav/.venv-wsl/bin/python')), '-m', 'uav.flight_server', '--config', linuxPath(config)]
      : ['-m', 'uav.flight_server', '--config', config], cwd: root, env: {} };
  store.saveConfig({ ...store.config, mcp: [...store.config.mcp.filter(item => item.id !== service.id), service] });
  return { config: store.publicConfig(), configFile: config, bridgeTokenFile: token };
}
