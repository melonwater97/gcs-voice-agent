"""Rebuild the existing local x500 simulation after a signed UI approval."""
from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--px4-root', type=Path, required=True)
    parser.add_argument('--latitude', type=float, required=True)
    parser.add_argument('--longitude', type=float, required=True)
    parser.add_argument('--altitude', type=float, required=True)
    args = parser.parse_args()
    root = args.px4_root.resolve()
    if root != Path('/opt/uav-sim/PX4-Autopilot') or not -90 <= args.latitude <= 90 or not -180 <= args.longitude <= 180 or not math.isfinite(args.altitude):
        raise ValueError('仅支持已核对的本地仿真目录和明确的 WGS84 位置')
    binary = root / 'build/px4_sitl_default/bin/px4'
    targets = []
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if (process / 'exe').resolve() == binary:
                targets.append(int(process.name))
        except OSError:
            continue
    if len(targets) != 1:
        raise RuntimeError('无法确认唯一的本地 PX4 SITL 进程')
    os.kill(targets[0], signal.SIGINT)
    deadline = time.monotonic() + 20
    while Path(f'/proc/{targets[0]}').exists() and time.monotonic() < deadline:
        time.sleep(.1)
    if Path(f'/proc/{targets[0]}').exists():
        raise RuntimeError('旧 SITL 未退出，未强杀或启动第二实例')
    # Shut down only the known default-world server from this PX4 deployment.
    world_processes = []
    for process in Path('/proc').glob('[0-9]*'):
        try:
            command = (process / 'cmdline').read_bytes().replace(b'\0', b' ').decode()
            if 'gz sim' in command and str(root / 'Tools/simulation/gz/worlds/default.sdf') in command:
                os.kill(int(process.name), signal.SIGINT)
                world_processes.append(process)
        except (OSError, UnicodeError):
            continue
    deadline = time.monotonic() + 20
    while any(process.exists() for process in world_processes) and time.monotonic() < deadline:
        time.sleep(.1)
    if any(process.exists() for process in world_processes):
        raise RuntimeError('旧 Gazebo 世界未退出，未启动第二实例')
    environment = ['env', 'HEADLESS=1', f'PX4_HOME_LAT={args.latitude}', f'PX4_HOME_LON={args.longitude}',
                   f'PX4_HOME_ALT={args.altitude}', 'make', 'px4_sitl', 'gz_x500']
    subprocess.run(['tmux', 'new-session', '-d', '-s', 'linghang-simulation', '-c', str(root), shlex.join(environment)], check=True)
    print(json.dumps({'requestedPosition': {'latitude': args.latitude, 'longitude': args.longitude, 'altitudeAmslM': args.altitude},
                      'restarted': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
