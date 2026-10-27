"""YAML-configured ground-station HTTP client used by the Python MCP server."""

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from shared.contracts import Task


class GroundStationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = ""
    timeout_seconds: float = Field(default=30, ge=1, le=300)
    token_env: str = "LH_GCS_TOKEN"
    simulation: bool = False
    agent_id: str = Field(default="AGENT-01", min_length=1, max_length=64)
    station_id: str = Field(default="GS-01", min_length=1, max_length=64)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip("/")
        url = urlsplit(value)
        if value and (url.scheme not in {"http", "https"} or not url.hostname
                      or url.username or url.password or url.query or url.fragment):
            raise ValueError("base_url 必须是无凭据、查询参数及片段的 HTTP(S) 基础地址")
        if value:
            _ = url.port
        return value

    @field_validator("token_env")
    @classmethod
    def valid_env(cls, value):
        if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("token_env 必须是环境变量名，令牌不要直接写进 YAML")
        return value


class ServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ground_station: GroundStationConfig


def load_config(file: Path) -> GroundStationConfig:
    if file.stat().st_size > 65536:
        raise ValueError("MCP YAML 配置不能超过 64 KiB")
    return ServiceConfig.model_validate(yaml.safe_load(file.read_text(encoding="utf-8"))).ground_station


def atomic_json(file: Path, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(f".{file.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(file)
    finally:
        temporary.unlink(missing_ok=True)


class GroundStationHTTP:
    def __init__(self, config: GroundStationConfig, request_dir: Path):
        self.config = config
        self.request_dir = request_dir
        self._lock = threading.RLock()

    def call(self, name: Literal["create_task", "upload_task", "execute_task"], arguments: dict) -> dict:
        config = self.config
        if not config.base_url:
            raise ValueError("请在 MCP 服务的 YAML 配置中填写 ground_station.base_url")
        payload = {"type": name, **arguments}
        identity = {"url": config.base_url, "agent": config.agent_id, "station": config.station_id, "payload": payload}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        file = self.request_dir / f"{digest}.json"
        with self._lock:
            previous = json.loads(file.read_text(encoding="utf-8")) if file.exists() else None
            uncertain = previous and previous["status"] in {"sending", "unknown"}
            if uncertain and time.time() - previous["created_at"] >= 86400:
                raise ValueError(f"原请求 {previous['envelope']['header']['msg_id']} 已超过 24 小时，请先在地面站核对结果")
            envelope = previous["envelope"] if uncertain else {
                "header": {"msg_id": f"MSG-{uuid4()}", "msg_type": "TASK_COMMAND", "version": "2.0.0",
                           "src": {"type": "BACKGROUND", "id": config.agent_id},
                           "dst": {"type": "FRONT", "id": config.station_id}},
                "payload": payload,
            }
            entry = {"status": "sending", "created_at": previous["created_at"] if uncertain else time.time(), "envelope": envelope}
            atomic_json(file, entry)
            request_id = envelope["header"]["msg_id"]
            headers = {"Content-Type": "application/json"}
            token = os.environ.get(config.token_env, "") if config.token_env else ""
            if token:
                headers["Authorization"] = f"Bearer {token}"
            confirmed = False
            try:
                response = httpx.post(config.base_url + "/api/tasks", json=envelope, headers=headers,
                                      timeout=config.timeout_seconds, trust_env=False, follow_redirects=False)
                message = response.json()
                header, result = message.get("header", {}), message.get("payload", {})
                if (header.get("msg_type") != "TASK_RESULT" or header.get("version") != "2.0.0"
                        or header.get("src") != {"type": "FRONT", "id": config.station_id}
                        or header.get("dst") != {"type": "BACKGROUND", "id": config.agent_id}
                        or type(result.get("code")) is not int or result["code"] != response.status_code):
                    raise ValueError("地面站回执格式、协议版本或路由不符合约定")
                if 400 <= response.status_code < 500:
                    entry.update(status="failed", result=message)
                    atomic_json(file, entry)
                    confirmed = True
                    raise ValueError(result.get("message", "地面站拒绝请求"))
                if response.status_code != 200:
                    raise ValueError(f"地面站返回 HTTP {response.status_code}")
                expected = {"create_task": "draft", "upload_task": "uploaded", "execute_task": "executing"}[name]
                if (result.get("type") != name or result.get("status") != expected
                        or not re.fullmatch(r"TASK-[0-9a-f]{32}", result.get("task_id", ""))
                        or ("task_id" in arguments and result["task_id"] != arguments["task_id"])):
                    raise ValueError("地面站回执中的操作、任务编号或状态不匹配")
                TypeAdapter(Task).validate_python(result.get("task"))
                entry.update(status="completed", result=message)
                atomic_json(file, entry)
                return {**result, "request_id": request_id, "simulation": config.simulation,
                        "source": "groundstation-http-simulator" if config.simulation else "groundstation-http"}
            except Exception as exc:
                if confirmed:
                    raise
                entry["status"] = "unknown"
                atomic_json(file, entry)
                raise ValueError(f"结果待确认：{exc}。原请求 {request_id}，核对或以原编号重试。") from exc
