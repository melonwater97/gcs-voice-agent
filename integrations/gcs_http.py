"""L3 adapter: assemble the interface message and send it to the GCS via HTTP."""

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from shared.config import DEFAULT_GCS_URL
from shared.contracts import CreateTaskPayload, CreateTaskResult, INTERFACE_FILE, TaskMessage


def build_message(payload: dict, interface_file: Path = INTERFACE_FILE) -> TaskMessage:
    validated = CreateTaskPayload.model_validate(payload)
    template = json.loads(interface_file.read_text(encoding="utf-8"))
    if not isinstance(template, dict) or not isinstance(template.get("header"), dict):
        raise ValueError("接口.json 必须包含 header 对象")
    header = dict(template["header"])
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    header["msg_id"] = f"MSG-{date}-{uuid4().hex}"
    return TaskMessage.model_validate({"header": header, "payload": validated.model_dump()})


class GCSHTTPClient:
    def __init__(self, base_url: str = DEFAULT_GCS_URL, interface_file: Path = INTERFACE_FILE):
        self.base_url = base_url.rstrip("/")
        self.interface_file = interface_file

    def create_task(self, payload: dict) -> dict:
        message = build_message(payload, self.interface_file).model_dump()
        try:
            # One request, no automatic retry of task creation.
            with httpx.Client(timeout=10.0, trust_env=False) as http:
                response = http.post(f"{self.base_url}/tasks", json=message)
        except httpx.HTTPError as exc:
            raise ValueError(f"地面站 HTTP 请求失败，请检查地面站 HTTP 服务：{exc}") from exc
        if response.status_code != 201:
            raise ValueError(f"地面站 HTTP 拒绝（{response.status_code}）：{response.text}")
        try:
            result = CreateTaskResult.model_validate(response.json())
        except ValueError as exc:
            raise ValueError(f"地面站返回结构无效：{exc}") from exc
        if result.message.model_dump() != message:
            raise ValueError("地面站返回的消息与发送的任务不一致")
        return result.model_dump()
