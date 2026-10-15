"""Simulated ground-station application, listening as an HTTP server."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from .backend import SimulatedGCS


MAX_BODY_BYTES = 1024 * 1024


def reject_constant(value: str):
    raise ValueError(f"非法 JSON 数字：{value}")


class GCSHandler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/health":
            self.reply(200, {"status": "ok", "service": "simulated-gcs"})
        else:
            self.reply(404, {"status": "error", "error": "接口不存在"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/tasks":
            self.reply(404, {"status": "error", "error": "接口不存在"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.reply(415, {"status": "error", "error": "需要 application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reply(400, {"status": "error", "error": "Content-Length 无效"})
            return
        if not 0 < length <= MAX_BODY_BYTES:
            self.reply(400, {"status": "error", "error": "请求体为空或超过1MB"})
            return
        try:
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length), parse_constant=reject_constant)
        except (ValueError, OSError) as exc:
            self.reply(400, {"status": "error", "error": f"JSON 请求无效：{exc}"})
            return
        if not self.server.quiet:
            print("[地面站 HTTP] 收到 JSON：" + json.dumps(body, ensure_ascii=False), flush=True)
        try:
            result = self.server.backend.create_task(body)
        except ValidationError as exc:
            self.reply(422, {"status": "error", "error": "任务消息校验失败",
                             "details": exc.errors(include_url=False, include_context=False)})
            return
        except OSError as exc:
            self.reply(500, {"status": "error", "error": f"任务保存失败：{exc}"})
            return
        if not self.server.quiet:
            print(f"[地面站 HTTP] 已创建 {result['task_id']}，保存到 {result['file']}", flush=True)
        self.reply(201, result)

    def log_message(self, fmt: str, *args) -> None:
        if not self.server.quiet:
            print("[地面站 HTTP] " + fmt % args, flush=True)


class GCSHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], output_dir: Path, *, quiet: bool = False):
        self.backend = SimulatedGCS(output_dir)
        self.quiet = quiet
        super().__init__(address, GCSHandler)

