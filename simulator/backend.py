"""Agent 侧的任务模拟：MCP 调用本地操作，结果经 Agent 返回 React 地面站。"""

import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import TypeAdapter

from shared.config import OUTPUT_DIR
from shared.contracts import Task, UploadTaskResult, ExecuteTaskResult, can_transition, named_task


class TaskStore:
    """本次 MCP 会话的草稿、模拟上传与执行状态；上传文件保存在 Agent 侧。"""

    def __init__(self, output_dir: Path = OUTPUT_DIR):
        self.output_dir = output_dir.expanduser().resolve()
        self._entries: dict[str, dict] = {}
        self._lock = RLock()

    def create(self, task) -> str:
        task = TypeAdapter(Task).validate_python(task)
        task_id = f"TASK-{uuid4().hex}"
        with self._lock:
            self._entries[task_id] = {"task": named_task(task, task_id), "status": "draft"}
        return task_id

    def entry(self, task_id: str) -> dict:
        entry = self._entries.get(task_id)
        if entry is None:
            raise ValueError(f"任务编号不存在或不在本会话中：{task_id}")
        return entry

    def require_transition(self, task_id: str, target: str) -> None:
        status = self.entry(task_id)["status"]
        if not can_transition(status, target):
            raise ValueError(f"任务当前状态为 {status}，不能转到 {target}，"
                             "请按 创建→上传→执行 顺序操作")

    def mark(self, task_id: str, status: str) -> None:
        self.require_transition(task_id, status)
        self.entry(task_id)["status"] = status

    def upload(self, task_id: str) -> dict:
        with self._lock:
            self.require_transition(task_id, "uploaded")
            path = self.output_dir / f"{task_id}.json"
            result = UploadTaskResult(status="uploaded", task_id=task_id,
                                      task=self.entry(task_id)["task"], file=str(path)).model_dump()
            encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            try:
                file = path.open("x", encoding="utf-8")
            except FileExistsError as exc:
                raise ValueError(f"任务文件已存在，未覆盖：{task_id}") from exc
            try:
                with file:
                    file.write(encoded)
            except OSError:
                path.unlink(missing_ok=True)
                raise
            self.mark(task_id, "uploaded")
            return result

    def execute(self, task_id: str) -> dict:
        with self._lock:
            self.require_transition(task_id, "executing")
            result = ExecuteTaskResult(task_id=task_id, status="executing").model_dump()
            self.mark(task_id, "executing")
            return result
