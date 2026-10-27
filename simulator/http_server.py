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
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

# Also support launching this file directly from ``simulator/``.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from shared.config import OUTPUT_DIR
from shared.contracts import Task, can_transition, named_task

PROTOCOL_VERSION = "2.0.0"
DEDUP_RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_AGENT_ID = "AGENT-01"
DEFAULT_STATION_ID = "GS-01"
MAX_REQUEST_BYTES = 1_048_576


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


def error_response(service: GroundStationSimulator, status: int,
                   message: str) -> JSONResponse:
    _, response = service.handle(None)
    response["payload"] = {"code": status, "message": message}
    return JSONResponse(status_code=status, content=response)


def create_app(service: GroundStationSimulator) -> FastAPI:
    app = FastAPI(
        title="Ground Station Simulator",
        version=PROTOCOL_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.api_route("/{full_path:path}",
                   methods=["POST", "GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"],
                   include_in_schema=False)
    async def dispatch(full_path: str, request: Request) -> JSONResponse:
        if full_path != "api/tasks":
            if request.method == "POST":
                return error_response(service, 404, "路径不存在")
            return error_response(service, 405, "仅支持 POST 方法")

        if request.method != "POST":
            return error_response(service, 405, "仅支持 POST 方法")

        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return error_response(service, 415, "Content-Type 必须为 application/json")

        raw = await request.body()
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            return error_response(service, 400, "JSON 请求无效：请求体为空或超过 1 MiB")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return error_response(service, 400, f"JSON 请求无效：{exc}")

        try:
            status, response = service.handle(body)
        except OSError as exc:
            return error_response(service, 503, f"模拟服务状态保存失败：{exc}")
        return JSONResponse(status_code=status, content=response)

    return app


def main() -> None:
    host = os.environ.get("GCS_SIM_HOST", "127.0.0.1")
    port = int(os.environ.get("GCS_SIM_PORT", "18003"))
    state_path = Path(os.environ.get("GCS_SIM_STATE_PATH", str(OUTPUT_DIR / "gcs_simulator_state.json")))
    service = GroundStationSimulator(
        state_path,
        agent_id=os.environ.get("GCS_AGENT_ID", DEFAULT_AGENT_ID),
        station_id=os.environ.get("GCS_STATION_ID", DEFAULT_STATION_ID),
    )
    print(f"地面站 HTTP 模拟服务监听 http://{host}:{port}/api/tasks", flush=True)
    uvicorn.run(create_app(service), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
