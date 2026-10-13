"""L4: the HTTP ground-station simulator validates and stores received messages."""

import json
from pathlib import Path
from uuid import uuid4

from contracts import TaskMessage


class SimulatedGCS:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def create_task(self, message: dict) -> dict:
        # Authoritative validation also happens at the L4 execution boundary.
        validated = TaskMessage.model_validate(message)
        task_id = f"TASK-{uuid4().hex}"
        message = validated.model_dump()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{task_id}.json"
        with path.open("x", encoding="utf-8") as file:
            json.dump(message, file, ensure_ascii=False, indent=2)
            file.write("\n")
        return {"task_id": task_id, "status": "created", "message": message,
                "file": str(path)}
