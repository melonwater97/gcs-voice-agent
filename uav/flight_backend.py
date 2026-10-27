"""Single-reader MAVLink connection and simulation-only flight operations."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import httpx
from pymavlink import mavutil


def atomic(file: Path, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(f'.{file.name}.{secrets.token_hex(8)}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temporary.replace(file)
    finally:
        temporary.unlink(missing_ok=True)


class MavlinkConnection:
    """One receiver owns the socket; ACK and telemetry consumers use a shared event queue."""
    def __init__(self, config):
        self.config = config
        self.connection = None
        self.condition = threading.Condition()
        self.events = deque(maxlen=4096)
        self.latest = {}
        self.sequence = 0
        self.send_lock = threading.Lock()
        self.open_lock = threading.Lock()

    def open(self):
        with self.open_lock:
            if self.connection:
                return
            self.connection = mavutil.mavlink_connection('udpin:127.0.0.1:14540', source_system=254, source_component=195)
            self.connection.destination_addr = ('127.0.0.1', 14580)
            # A pymavlink udpin writer otherwise sends to every learned peer. Keep all
            # outbound traffic on the kernel-verified local PX4 socket, including before heartbeat.
            socket = self.connection.port
            class FixedPeer:
                def write(self, content):
                    return socket.sendto(content, ('127.0.0.1', 14580))
            self.connection.mav.file = FixedPeer()
            self.connection.target_system = self.config['target_system']
            self.connection.target_component = 1
            threading.Thread(target=self._receive, daemon=True).start()

    def _receive(self):
        while self.connection:
            message = self.connection.recv_match(blocking=True, timeout=1)
            if not message or message.get_srcSystem() != self.config['target_system'] or message.get_srcComponent() != 1:
                continue
            with self.condition:
                self.sequence += 1
                self.events.append((self.sequence, time.monotonic(), message))
                self.latest[message.get_type()] = (time.monotonic(), message)
                self.condition.notify_all()

    def wait(self, types, after=0, timeout=5, predicate=lambda message: True):
        deadline = time.monotonic() + timeout
        with self.condition:
            while time.monotonic() < deadline:
                for sequence, _, message in self.events:
                    if sequence > after and message.get_type() in types and predicate(message):
                        return message
                self.condition.wait(max(0, deadline - time.monotonic()))
        raise TimeoutError('未收到预期 MAVLink 回执：' + ','.join(types))

    def send(self, method, *args):
        self.open()
        with self.send_lock:
            getattr(self.connection.mav, method)(*args)

    def fresh(self, name, seconds=5):
        with self.condition:
            item = self.latest.get(name)
        if not item or time.monotonic() - item[0] > seconds:
            raise RuntimeError(f'{name} 遥测缺失或过期')
        return item[1]

    def command(self, command, *parameters):
        cursor = self.sequence
        values = list(parameters) + [0] * (7 - len(parameters))
        self.send('command_long_send', self.config['target_system'], 1, command, 0, *values)
        ack = self.wait(['COMMAND_ACK'], cursor, predicate=lambda message: message.command == command and getattr(message, 'target_system', 0) in [0, 254])
        if ack.result != mavutil.mavlink.MAV_RESULT_ACCEPTED:
            raise RuntimeError(f'PX4 拒绝命令 {command}，结果 {ack.result}')

    def status(self):
        self.open()
        for message_id in [0, 33, 245, 242, 1]:
            self.send('command_long_send', self.config['target_system'], 1, 512, 0, message_id, 0, 0, 0, 0, 0, 0)
        deadline = time.monotonic() + 6
        while True:
            try:
                heartbeat, position, extended = self.fresh('HEARTBEAT'), self.fresh('GLOBAL_POSITION_INT'), self.fresh('EXTENDED_SYS_STATE')
                if heartbeat.type != 2 or heartbeat.autopilot != 12:
                    raise RuntimeError('首版仅支持当前已部署的 PX4 x500 四旋翼仿真')
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                with self.condition:
                    self.condition.wait(.2)
        return {'observedAt': int(time.time() * 1000), 'armed': bool(heartbeat.base_mode & 128),
                'displayName': 'PX4 SITL · x500',
                'vehicleTimeMs': position.time_boot_ms,
                'landed': extended.landed_state == 1, 'mode': heartbeat.custom_mode,
                'position': {'latitude': position.lat / 1e7, 'longitude': position.lon / 1e7, 'altitudeAmslM': position.alt / 1000},
                'relativeAltitudeM': position.relative_alt / 1000,
                'speedMS': math.hypot(position.vx, position.vy) / 100}

    @staticmethod
    def mission_items(plan):
        result = []
        for item in plan['mission']['items']:
            if item.get('type') != 'SimpleItem' or item.get('command') not in [16, 22] or item.get('frame') not in [3, 6]:
                raise ValueError('仅支持简单起飞/航点与相对高度')
            p = item['params']
            if len(p) != 7 or item.get('autoContinue') is not True or any(value not in (0, None) for value in p[:4]):
                raise ValueError('任务项参数不在支持范围')
            result.append({'command': item['command'], 'frame': 6, 'params': [0, 0, 0, 0, float(p[4]), float(p[5]), float(p[6])]})
        if not result or result[0]['command'] != 22:
            raise ValueError('缺少明确起飞任务项')
        return result

    def upload(self, items):
        self.open(); target = self.config['target_system']; cursor = self.sequence
        self.send('mission_count_send', target, 1, len(items), 0)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            message = self.wait(['MISSION_REQUEST_INT', 'MISSION_REQUEST', 'MISSION_ACK'], cursor, timeout=8)
            cursor = self.sequence
            if message.get_type() == 'MISSION_ACK':
                if message.type != 0:
                    raise RuntimeError(f'航线上传被拒绝：MISSION_ACK={message.type}')
                return
            if not 0 <= message.seq < len(items):
                raise ValueError('飞控请求了越界任务项')
            item = items[message.seq]; params = item['params']
            if message.get_type() == 'MISSION_REQUEST':
                self.send('mission_item_send', target, 1, message.seq, 3, item['command'], 0, 1, *params, 0)
            else:
                self.send('mission_item_int_send', target, 1, message.seq, 6, item['command'], 0, 1,
                          *params[:4], round(params[4] * 1e7), round(params[5] * 1e7), params[6], 0)
        raise TimeoutError('上传航线超时')

    def read_mission(self):
        target = self.config['target_system']; cursor = self.sequence
        self.send('mission_request_list_send', target, 1, 0)
        count = self.wait(['MISSION_COUNT'], cursor).count
        if count > 200:
            raise ValueError('飞控任务项超过首版上限')
        items = []
        for seq in range(count):
            cursor = self.sequence
            self.send('mission_request_int_send', target, 1, seq, 0)
            item = self.wait(['MISSION_ITEM_INT'], cursor, predicate=lambda message: message.seq == seq)
            items.append({'command': item.command, 'frame': 6 if item.frame in [3, 6] else item.frame,
                          'params': [item.param1, item.param2, item.param3, item.param4, item.x / 1e7, item.y / 1e7, item.z]})
        self.send('mission_ack_send', target, 1, 0, 0)
        return items

    @staticmethod
    def matching(expected, actual):
        return len(expected) == len(actual) and all(a['command'] == b['command'] and a['frame'] == b['frame']
            and all(math.isclose(x, y, abs_tol=1e-6 if index in [4, 5] else .01) for index, (x, y) in enumerate(zip(a['params'], b['params']))) for a, b in zip(expected, actual))

    def mode(self, value):
        cursor = self.sequence
        self.send('set_mode_send', self.config['target_system'], 1, value)
        self.wait(['HEARTBEAT'], cursor, timeout=8, predicate=lambda message: message.custom_mode == value)


class SimulationBackend:
    def __init__(self, config, data_dir: Path):
        self.config = config; self.data_dir = data_dir; data_dir.mkdir(parents=True, exist_ok=True)
        self.link = MavlinkConnection(config)
        self.key = Path(config['authority_key_file']).read_bytes()
        self.action_lock = threading.Lock(); self.execution = None

    def attest(self):
        root = Path(self.config['px4_root']).resolve()
        expected = root / 'build/px4_sitl_default/bin/px4'
        matches = []
        for process in Path('/proc').glob('[0-9]*'):
            try:
                if (process / 'exe').resolve() == expected:
                    udp = Path('/proc/net/udp').read_text(encoding='utf-8').splitlines()[1:]
                    inodes = {line.split()[9] for line in udp if line.split()[1].split(':')[1] == f'{14580:04X}'}
                    sockets = {os.readlink(file) for file in (process / 'fd').glob('*')}
                    if not any(f'socket:[{inode}]' in sockets for inode in inodes):
                        continue
                    start = (process / 'stat').read_text(encoding='utf-8').split()[21]
                    matches.append(f'SITL:{process.name}:{start}:sys{self.config["target_system"]}')
            except (OSError, IndexError):
                continue
        if len(matches) != 1:
            raise RuntimeError('必须核验唯一的本地 PX4 SITL 进程；不支持真机或多机')
        return matches[0]

    def status(self):
        vehicle_id = self.attest(); status = self.link.status()
        return {**status, 'vehicleId': vehicle_id, 'simulationVerified': True}

    def bridge(self, method='GET', payload=None):
        url = self.config['qgc_bridge_url']
        token = Path(self.config['bridge_token_file']).read_text(encoding='utf-8').strip()
        response = httpx.request(method, url.rstrip('/') + '/snapshot' if method == 'GET' else url.rstrip('/') + '/preview',
                                 headers={'Authorization': f'Bearer {token}'}, json=payload, timeout=8, trust_env=False)
        response.raise_for_status()
        return response.json()

    def verified_grant(self, token):
        body, signature = token.split('.')
        if not hmac.compare_digest(signature, hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest()):
            raise ValueError('批准签名无效')
        grant = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if grant.get('expiresAt', 0) <= time.time() * 1000 or grant.get('action') not in ['origin', 'upload', 'start']:
            raise ValueError('批准已过期或动作无效')
        if not all(isinstance(grant.get(key), str) and len(grant[key]) <= 200 for key in ['nonce', 'flightId', 'vehicleId', 'planDigest']):
            raise ValueError('批准字段无效')
        if not all(char in 'abcdef0123456789-' for char in grant['nonce'] + grant['flightId']):
            raise ValueError('批准编号无效')
        return grant

    def execute(self, token):
        grant = self.verified_grant(token)
        with self.action_lock:
            journal = self.data_dir / 'approvals' / f'{grant["nonce"]}.json'
            if journal.exists():
                old = json.loads(journal.read_text(encoding='utf-8'))
                if old['status'] == 'completed':
                    return old['result']
                raise ValueError('该批准已消费，结果需要核对，禁止重放')
            status = self.status(); snapshot = self.bridge()
            if status['vehicleId'] != grant['vehicleId'] or status['armed'] or not status['landed']:
                raise ValueError('飞行器身份、落地或解锁状态不符合批准条件')
            origin = grant.get('origin')
            if not origin or abs(origin['latitude'] - status['position']['latitude']) > .00001 or abs(origin['longitude'] - status['position']['longitude']) > .00001:
                raise ValueError('实际起飞位置与批准时不一致')
            if snapshot['digest'] != grant['planDigest'] or snapshot['revision'] != grant['qgcRevision']:
                raise ValueError('QGC 草稿版本与批准不一致')
            atomic(journal, {'status': 'executing', 'grant': grant})
            try:
                if grant['action'] == 'origin':
                    position = grant['payload']['position']
                    if position != snapshot.get('selectedPosition') or not math.isfinite(position.get('altitudeAmslM', math.nan)):
                        raise ValueError('选点或海拔无效，不能重建实际仿真位置')
                    result = self.relocate(position)
                else:
                    items = self.link.mission_items(snapshot['plan'])
                    self.check_limits(items, snapshot['plan'], grant['limits'], status['position'])
                    if grant['action'] == 'upload':
                        self.link.upload(items)
                        if not self.link.matching(items, self.link.read_mission()):
                            raise RuntimeError('上传后读回航线与批准内容不一致')
                        result = {'verified': True}
                        atomic(self.data_dir / 'uploaded.json', {'digest': snapshot['digest'], 'vehicleId': status['vehicleId']})
                    else:
                        uploaded_file = self.data_dir / 'uploaded.json'
                        uploaded = json.loads(uploaded_file.read_text(encoding='utf-8')) if uploaded_file.exists() else {}
                        if uploaded != {'digest': snapshot['digest'], 'vehicleId': status['vehicleId']} or not self.link.matching(items, self.link.read_mission()):
                            raise RuntimeError('当前机载航线不匹配，拒绝启动')
                        result = self.start(grant, items, snapshot['plan'])
                result.update(status='completed', action=grant['action'], vehicle=self.status())
                atomic(journal, {'status': 'completed', 'result': result})
                return result
            except Exception as error:
                if grant['action'] == 'start' and self.execution and self.execution['flightId'] == grant['flightId']:
                    self.execution['status'] = 'failed'; self.execution['alerts'].append(f'启动回执不完整，请人工核对：{error}'); self.save_execution()
                atomic(journal, {'status': 'unknown', 'message': str(error)})
                raise

    @staticmethod
    def check_limits(items, plan, limits, origin):
        from math import asin, cos, radians, sin, sqrt
        def distance(a, b):
            lat = radians(b[0] - a[0]); lon = radians(b[1] - a[1])
            return 12742000 * asin(min(1, sqrt(sin(lat/2)**2 + cos(radians(a[0])) * cos(radians(b[0])) * sin(lon/2)**2)))
        if not all(isinstance(limits.get(name), (int, float)) and not isinstance(limits[name], bool) and math.isfinite(limits[name]) and limits[name] > 0 for name in ['maxAltitudeM', 'maxSpeedMS', 'maxDistanceM', 'maxDurationS']):
            raise ValueError('缺少有效的仿真限值')
        speed = plan['mission'].get('hoverSpeed')
        if not isinstance(speed, (int, float)) or not 0 < speed <= limits['maxSpeedMS']:
            raise ValueError('速度未配置或超过限值')
        home = origin['latitude'], origin['longitude']; previous = home; length = 0
        for item in items:
            p = item['params']; point = p[4], p[5]
            if not -90 <= p[4] <= 90 or not -180 <= p[5] <= 180 or not 0 < p[6] <= limits['maxAltitudeM'] or distance(home, point) > limits['maxDistanceM']:
                raise ValueError('航线位置或高度超出约定限值')
            length += distance(previous, point); previous = point
        length += distance(previous, home)
        if length / speed > limits['maxDurationS']:
            raise ValueError('估计水平航程超时')

    def start(self, grant, items, plan):
        if self.execution and self.execution.get('status') == 'running':
            raise RuntimeError('已有仿真任务在执行')
        self.execution = {'flightId': grant['flightId'], 'executionId': secrets.token_hex(16), 'status': 'running',
                          'startedMonotonic': time.monotonic(),
                          'startedAt': int(time.time()*1000), 'vehicleId': grant['vehicleId'], 'alerts': [], 'samples': [],
                          'home': self.status()['position'], 'baselineLogs': self.log_files(), 'lastWaypoint': len(items)-1,
                          'limits': grant['limits'], 'phase': 'starting', 'seenAirborne': False}
        self.save_execution()
        # The combined UI approval authorizes both arming and starting this verified mission.
        self.link.mode(0x00030000)  # Verify Position mode before arming, so a stale Mission mode cannot launch early.
        self.link.command(178, 1, plan['mission']['hoverSpeed'], -1)
        self.link.command(400, 1)
        self.link.wait(['HEARTBEAT'], self.link.sequence, timeout=8, predicate=lambda message: bool(message.base_mode & 128))
        self.link.mode(0x04040000)
        self.execution['phase'] = 'mission'
        self.save_execution()
        threading.Thread(target=self.monitor, daemon=True).start()
        return {'executionId': self.execution['executionId']}

    def save_execution(self):
        atomic(self.data_dir / 'executions' / f'{self.execution["flightId"]}.json', self.execution)

    def monitor(self):
        while self.execution and self.execution['status'] == 'running':
            execution = self.execution
            try:
                status = self.status()
                if status['vehicleId'] != execution['vehicleId']:
                    raise RuntimeError('仿真进程身份变化，请人工接管')
                if status['relativeAltitudeM'] > execution['limits']['maxAltitudeM'] or status['speedMS'] > execution['limits']['maxSpeedMS']:
                    raise RuntimeError('实际高度或速度超出已批准场景限值，请人工接管')
                execution['samples'].append({'at': int(time.time()*1000), **status})
                execution['seenAirborne'] |= status['landed'] is False
                if execution['phase'] == 'mission':
                    if status['mode'] != 0x04040000:
                        raise RuntimeError('任务模式发生变化，未发送额外处置指令')
                    reached = self.link.latest.get('MISSION_ITEM_REACHED')
                    if reached and reached[0] >= execution['startedMonotonic'] and reached[1].seq == execution['lastWaypoint']:
                        # Deterministic normal completion, already included in the approved mission.
                        self.link.mode(0x05040000); execution['phase'] = 'rtl'
                elif execution['phase'] == 'rtl' and status['mode'] not in [0x05040000, 0x06040000] and status['armed']:
                    raise RuntimeError('返航过程中模式变化，请人工接管')
                if execution['seenAirborne'] and status['landed'] and not status['armed']:
                    execution['status'] = 'completed'; execution['endedAt'] = int(time.time()*1000)
                if time.time()*1000 - execution['startedAt'] > execution['limits']['maxDurationS']*1000 and execution['status'] == 'running':
                    raise RuntimeError('达到约定任务时长，停止额外控制并请求人工接管')
            except Exception as error:
                execution['status'] = 'failed'; execution['alerts'].append(str(error))
            self.save_execution()
            threading.Event().wait(.5)

    def execution_status(self, flight_id):
        file = self.data_dir / 'executions' / f'{flight_id}.json'
        if not file.exists():
            return {'status': 'unknown', 'alerts': ['没有可核对的执行记录']}
        result = json.loads(file.read_text(encoding='utf-8'))
        if result['status'] == 'running' and (not self.execution or self.execution['flightId'] != flight_id):
            result['status'] = 'unknown'; result['alerts'].append('执行服务已重启，需要人工核对；不重放飞行命令')
        return {key: result.get(key) for key in ['status', 'phase', 'alerts', 'startedAt', 'endedAt', 'executionId']}

    def log_files(self):
        root = Path(self.config['px4_root']) / 'build/px4_sitl_default/rootfs/log'
        return {str(file): {'size': file.stat().st_size, 'mtime': file.stat().st_mtime} for file in root.glob('*/*.ulg')
                if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(root.resolve())}

    def relocate(self, position):
        # Restart implementation is explicit and only reachable after a signed UI approval.
        runner = Path(__file__).with_name('relocate_simulation.py')
        result = subprocess.run([os.sys.executable, str(runner), '--px4-root', self.config['px4_root'],
                                 '--latitude', str(position['latitude']), '--longitude', str(position['longitude']),
                                 '--altitude', str(position['altitudeAmslM'])], check=True, capture_output=True, text=True, timeout=90)
        with self.link.condition:
            self.link.latest.clear()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                status = self.status()
                if status['landed'] and not status['armed'] and abs(status['position']['latitude'] - position['latitude']) < .00002 and abs(status['position']['longitude'] - position['longitude']) < .00002:
                    return {**json.loads(result.stdout), 'positionVerified': True}
            except (RuntimeError, TimeoutError):
                pass
            time.sleep(.5)
        raise RuntimeError('重建后的实际位置未核对成功，请人工检查，不自动重试重建')
