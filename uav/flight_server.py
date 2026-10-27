"""Official MCP SDK server for the supervised PX4 simulation workflow."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from typing import Any
from pathlib import Path

import yaml
from mcp.server import MCPServer

from uav.flight_backend import SimulationBackend, atomic


def create_server(config, data_dir):
    backend = SimulationBackend(config, data_dir)
    server = MCPServer('linghang-px4-simulation')

    @server.tool()
    def query_status() -> dict[str, Any]:
        """只读核验本地 PX4 SITL 身份、实际位置、落地、解锁状态和遥测时效。"""
        return backend.status()

    @server.tool()
    def qgc_snapshot() -> dict[str, Any]:
        """读取 QGC 当前地图选点和未上传的真实航线草稿、摘要及版本。"""
        return backend.bridge()

    @server.tool()
    def preview_mission(plan: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        """在 QGC 中预览航线草稿，不上传飞控；有并发修改则拒绝覆盖。"""
        status = backend.status()
        if status['armed'] or not status['landed']:
            raise ValueError('只允许已落地、未解锁时修改草稿')
        return backend.bridge('POST', {'plan': plan, 'expectedRevision': expected_revision})

    @server.tool()
    def execute_authorized_action(grant: str) -> dict[str, Any]:
        """内部执行入口：必须有应用签发的单次界面批准。对模型隐藏，不能通过回填确认 token 执行。"""
        return backend.execute(grant)

    @server.tool()
    def execution_status(flight_id: str) -> dict[str, Any]:
        """只读查询本次批准后的执行记录，异常时仅报告，不临时发起处置。"""
        if not all(char in 'abcdef0123456789-' for char in flight_id) or len(flight_id) != 36:
            raise ValueError('无效飞行编号')
        return backend.execution_status(flight_id)

    @server.tool()
    def collect_flight_logs(flight_id: str) -> dict[str, Any]:
        """实际落地并解除解锁后，读取与本次执行时间和飞行器关联的遥测及 PX4 ULog 清单。"""
        status = backend.status()
        if status['armed'] or not status['landed']:
            raise ValueError('飞行尚未结束，不能收集最终报告数据')
        if len(flight_id) != 36 or not all(char in 'abcdef0123456789-' for char in flight_id):
            raise ValueError('无效飞行编号')
        file = data_dir / 'executions' / f'{flight_id}.json'
        execution = json.loads(file.read_text(encoding='utf-8'))
        if execution['status'] not in ['completed', 'failed', 'running'] or execution['vehicleId'] != status['vehicleId']:
            raise ValueError('缺少本次飞行已完成的匹配记录')
        samples = execution.get('samples', [])
        missing = list(execution.get('alerts', []))
        if execution['status'] != 'completed':
            missing.append('执行没有完整成功回执；此报告用于异常结束后的数据归档，不宣称任务成功')
        if not samples:
            missing.append('缺少与本次飞行关联的遥测样本')
        metrics = {
            'source': 'approved-flight-telemetry', 'sampleCount': len(samples),
            'durationSeconds': (execution.get('endedAt', samples[-1]['at'] if samples else execution['startedAt']) - execution['startedAt']) / 1000,
            'maxRelativeAltitudeM': max((sample['relativeAltitudeM'] for sample in samples), default=None),
            'maxHorizontalSpeedMS': max((sample['speedMS'] for sample in samples), default=None),
        }
        log_root = data_dir / 'logs' / flight_id; log_root.mkdir(parents=True, exist_ok=True)
        telemetry = log_root / 'telemetry.json'; atomic(telemetry, execution)
        artifacts = [{'id': 'telemetry', 'name': 'telemetry.json', 'size': telemetry.stat().st_size,
                      'sha256': hashlib.sha256(telemetry.read_bytes()).hexdigest(), 'path': str(telemetry)}]
        for name, info in backend.log_files().items():
            before = execution.get('baselineLogs', {}).get(name)
            if info['mtime'] * 1000 >= execution['startedAt'] - 2000 and (before is None or info['size'] > before['size']):
                log = Path(name)
                # Keep an immutable copy for the report, not a still-changing logger file.
                target = log_root / log.name; target.write_bytes(log.read_bytes())
                artifacts.append({'id': hashlib.sha256(name.encode()).hexdigest(), 'name': log.name, 'size': target.stat().st_size,
                                  'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'path': str(target)})
        if len(artifacts) == 1:
            missing.append('未找到可关联的 PX4 ULog；保留本次飞行遥测，不以其他飞行日志替代')
        manifest = {'flightId': flight_id, 'vehicleId': status['vehicleId'], 'metrics': metrics, 'missing': missing, 'artifacts': artifacts}
        atomic(log_root / 'manifest.json', manifest)
        return {**manifest, 'artifacts': [{key: value for key, value in artifact.items() if key != 'path'} for artifact in artifacts]}

    @server.tool()
    def read_log_chunk(flight_id: str, log_id: str, offset: int = 0, length: int = 262144) -> dict[str, Any]:
        """只读下载本次已归档日志的有界片段，不接受任意文件路径。"""
        if len(flight_id) != 36 or not all(char in 'abcdef0123456789-' for char in flight_id):
            raise ValueError('无效飞行编号')
        if offset < 0 or not 1 <= length <= 524288:
            raise ValueError('日志片段范围无效')
        root = data_dir / 'logs' / flight_id
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        artifact = next(item for item in manifest['artifacts'] if item['id'] == log_id)
        file = Path(artifact['path']).resolve()
        if file.parent != root.resolve() or file.is_symlink():
            raise ValueError('日志路径不在归档目录')
        with file.open('rb') as stream:
            stream.seek(offset); content = stream.read(length)
        return {'offset': offset, 'data': base64.b64encode(content).decode(), 'length': len(content)}

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding='utf-8'))
    if config.get('simulation_only') is not True or config.get('target_system') != 1:
        raise ValueError('首版仅支持本地单机 SITL，目标系统编号必须明确为 1')
    create_server(config, Path(config['data_dir'])).run(transport='stdio')


if __name__ == '__main__':
    main()
