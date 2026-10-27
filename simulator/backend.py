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

    def __init__(self, output_dir: Path = OUTPUT_DIR, state_path: Path | None = None):
        self.output_dir = output_dir.expanduser().resolve()
        self.state_path = state_path.expanduser().resolve() if state_path else None
        self._entries: dict[str, dict] = {}
        self._lock = RLock()
        if self.state_path and self.state_path.exists():
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            if saved.get("version") != 1:
                raise ValueError("不支持的任务模拟状态版本")
            for task_id, entry in saved["tasks"].items():
                if entry["status"] not in {"draft", "uploaded", "executing", "completed", "aborted"}:
                    raise ValueError("任务模拟状态无效")
                self._entries[task_id] = {
                    "task": TypeAdapter(Task).validate_python(entry["task"]),
                    "status": entry["status"],
                }

    def _persist(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(f".{self.state_path.name}.{uuid4().hex}.tmp")
        content = {"version": 1, "tasks": {
            task_id: {"task": entry["task"].model_dump(), "status": entry["status"]}
            for task_id, entry in self._entries.items()
        }}
        try:
            temporary.write_text(json.dumps(content, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            temporary.replace(self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, task) -> str:
        task = TypeAdapter(Task).validate_python(task)
        task_id = f"TASK-{uuid4().hex}"
        with self._lock:
            self._entries[task_id] = {"task": named_task(task, task_id), "status": "draft"}
            try:
                self._persist()
            except OSError:
                self._entries.pop(task_id)
                raise
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
        previous = self.entry(task_id)["status"]
        self.entry(task_id)["status"] = status
        try:
            self._persist()
        except OSError:
            self.entry(task_id)["status"] = previous
            raise

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
            try:
                self.mark(task_id, "uploaded")
            except OSError:
                path.unlink(missing_ok=True)
                raise
            return result

    def execute(self, task_id: str) -> dict:
        with self._lock:
            self.require_transition(task_id, "executing")
            result = ExecuteTaskResult(task_id=task_id, status="executing").model_dump()
            self.mark(task_id, "executing")
            return result
