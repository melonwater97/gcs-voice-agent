"""A persistent HTTP simulator for the ground-station task API (protocol 2.0.0).

Run with ``python -m simulator.http_server``. The simulator exposes the three
documented operations through ``POST /api/tasks`` and does not connect to the
assistant's MCP server.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

# Also support launching this file directly from ``simulator/``.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from shared.config import OUTPUT_DIR
from shared.contracts import Task, can_transition, named_task

PROTOCOL_VERSION = "2.0.0"
DEDUP_RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_AGENT_ID = "AGENT-01"
DEFAULT_STATION_ID = "GS-01"
class OperationError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Endpoint(StrictModel):
    type: Literal["FRONT", "BACKGROUND"]
    id: str = Field(min_length=1, max_length=64)


class RequestHeader(StrictModel):
    msg_id: str = Field(pattern=r"^MSG-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    msg_type: Literal["TASK_COMMAND"]
    version: Literal["2.0.0"]
    src: Endpoint
    dst: Endpoint


class RequestEnvelope(StrictModel):
    header: RequestHeader
    payload: dict[str, Any]


class CreatePayload(StrictModel):
    type: Literal["create_task"]
    task: Any


class TaskIdPayload(StrictModel):
    type: Literal["upload_task", "execute_task"]
    task_id: str = Field(pattern=r"^TASK-[0-9a-f]{32}$")


class GroundStationSimulator:
    """Stateful task lifecycle and durable request de-duplication."""

    def __init__(self, state_path: Path, agent_id: str = DEFAULT_AGENT_ID,
                 station_id: str = DEFAULT_STATION_ID):
        self.state_path = state_path.expanduser().resolve()
        self.agent_id = agent_id
        self.station_id = station_id
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        if saved.get("version") != 1:
            raise ValueError("不支持的地面站模拟状态版本")
        self._tasks = saved.get("tasks", {})
        self._requests = saved.get("requests", {})

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(f".{self.state_path.name}.{uuid4().hex}.tmp")
        content = {"version": 1, "tasks": self._tasks, "requests": self._requests}
        try:
            temporary.write_text(json.dumps(content, ensure_ascii=False, allow_nan=False),
                                 encoding="utf-8")
            temporary.replace(self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _prune_requests(self, now: float) -> bool:
        expired = [key for key, item in self._requests.items()
                   if now - item["created_at"] >= DEDUP_RETENTION_SECONDS]
        for key in expired:
            del self._requests[key]
        return bool(expired)

    def _response(self, request_header: RequestHeader, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "header": {
                "msg_id": f"MSG-{uuid4()}",
                "msg_type": "TASK_RESULT",
                "version": PROTOCOL_VERSION,
                "src": {"type": "FRONT", "id": self.station_id},
                "dst": request_header.src.model_dump(),
            },
            "payload": payload,
        }

    def handle(self, body: Any) -> tuple[int, dict[str, Any]]:
        """Validate, de-duplicate and apply one protocol request."""
        request_header: RequestHeader | None = None
        if isinstance(body, dict) and isinstance(body.get("header"), dict):
            try:
                request_header = RequestHeader.model_validate(body["header"])
            except ValidationError:
                pass

        def error(code: int, message: str) -> tuple[int, dict[str, Any]]:
            if request_header is None:
                response = {
                    "header": {
                        "msg_id": f"MSG-{uuid4()}", "msg_type": "TASK_RESULT",
                        "version": PROTOCOL_VERSION,
                        "src": {"type": "FRONT", "id": self.station_id},
                        "dst": {"type": "BACKGROUND", "id": self.agent_id},
                    },
                    "payload": {"code": code, "message": message},
                }
            else:
                response = self._response(request_header, {"code": code, "message": message})
            return code, response

        try:
            envelope = RequestEnvelope.model_validate(body)
        except ValidationError as exc:
            return error(400, f"请求信封无效：{self._validation_message(exc)}")
        request_header = envelope.header
        if (request_header.src.type != "BACKGROUND" or request_header.src.id != self.agent_id
                or request_header.dst.type != "FRONT" or request_header.dst.id != self.station_id):
            return error(400, "消息 src / dst 与地面站调用方向不符")

        operation = envelope.payload.get("type")
        if operation not in {"create_task", "upload_task", "execute_task"}:
            return error(400, "payload.type 必须为 create_task、upload_task 或 execute_task")
        try:
            if operation == "create_task":
                payload = CreatePayload.model_validate(envelope.payload)
                task = TypeAdapter(Task).validate_python(payload.task)
            else:
                payload = TaskIdPayload.model_validate(envelope.payload)
        except ValidationError as exc:
            code = 422 if operation == "create_task" else 400
            return error(code, f"请求 payload 无效：{self._validation_message(exc)}")

        serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        key = f"/api/tasks|{request_header.src.id}|{request_header.msg_id}"
        with self._lock:
            now = time.time()
            expired = self._prune_requests(now)
            cached = self._requests.get(key)
            if cached:
                if expired:
                    self._persist()
                if cached["request_hash"] != digest:
                    return error(409, "同一消息编号已用于不同请求")
                return cached["http_status"], cached["response"]

            previous_tasks = deepcopy(self._tasks)
            try:
                status, response = self._apply(operation, payload, task if operation == "create_task" else None,
                                               request_header)
            except OperationError as exc:
                self._tasks = previous_tasks
                status, response = error(exc.status, str(exc))
            self._requests[key] = {
                "created_at": now,
                "request_hash": digest,
                "http_status": status,
                "response": response,
            }
            try:
                self._persist()
            except OSError:
                self._tasks = previous_tasks
                self._requests.pop(key, None)
                raise
            return status, response

    def _apply(self, operation: str, payload: Any, task: Any,
               request_header: RequestHeader) -> tuple[int, dict[str, Any]]:
        if operation == "create_task":
            task_id = f"TASK-{uuid4().hex}"
            named = named_task(task, task_id)
            self._tasks[task_id] = {"task": named.model_dump(), "status": "draft"}
            result = {"type": operation, "code": 200, "message": "任务创建成功",
                      "task_id": task_id, "status": "draft", "task": named.model_dump()}
        else:
            task_id = payload.task_id
            if task_id not in self._tasks:
                raise OperationError(404, f"任务不存在：{task_id}")
            entry = self._tasks[task_id]
            target = "uploaded" if operation == "upload_task" else "executing"
            current = entry["status"]
            if not can_transition(current, target):
                raise OperationError(409, f"任务当前状态为 {current}，不能执行 {operation}")
            entry["status"] = target
            result = {
                "type": operation,
                "code": 200,
                "message": "任务上传成功" if operation == "upload_task" else "任务已开始执行",
                "task_id": task_id,
                "status": target,
                "task": entry["task"],
            }
        return 200, self._response(request_header, result)

    @staticmethod
    def _validation_message(exc: ValidationError) -> str:
        first = exc.errors(include_url=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        return f"{location}: {first['msg']}" if location else first["msg"]


def make_handler(service: GroundStationSimulator):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GroundStationSimulator/2.0"

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:
            if self.path != "/api/tasks":
                _, response = service.handle(None)
                response["payload"] = {"code": 404, "message": "路径不存在"}
                self._send_json(404, response)
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                status, response = service.handle(None)
                self._send_json(415, {"header": response["header"],
                                      "payload": {"code": 415, "message": "Content-Type 必须为 application/json"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_048_576:
                    raise ValueError("请求体为空或超过 1 MiB")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                status, response = service.handle(None)
                self._send_json(400, {"header": response["header"],
                                      "payload": {"code": 400, "message": f"JSON 请求无效：{exc}"}})
                return
            try:
                status, response = service.handle(body)
                self._send_json(status, response)
            except OSError as exc:
                status, response = service.handle(None)
                self._send_json(503, {"header": response["header"],
                                      "payload": {"code": 503, "message": f"模拟服务状态保存失败：{exc}"}})

        def do_GET(self) -> None:
            self._method_not_allowed()

        def do_HEAD(self) -> None:
            self._method_not_allowed()

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:
            self._method_not_allowed()

        def _method_not_allowed(self) -> None:
            _, response = service.handle(None)
            response["payload"] = {"code": 405, "message": "仅支持 POST 方法"}
            self._send_json(405, response)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[GCS simulator] {self.address_string()} - {format % args}", file=sys.stderr)

    return Handler


def main() -> None:
    host = os.environ.get("GCS_SIM_HOST", "127.0.0.1")
    port = int(os.environ.get("GCS_SIM_PORT", "18003"))
    state_path = Path(os.environ.get("GCS_SIM_STATE_PATH", str(OUTPUT_DIR / "gcs_simulator_state.json")))
    service = GroundStationSimulator(
        state_path,
        agent_id=os.environ.get("GCS_AGENT_ID", DEFAULT_AGENT_ID),
        station_id=os.environ.get("GCS_STATION_ID", DEFAULT_STATION_ID),
    )
    server = ThreadingHTTPServer((host, port), make_handler(service))
    print(f"地面站 HTTP 模拟服务监听 http://{host}:{port}/api/tasks", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
