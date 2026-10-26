"""Agent 服务 HTTP 层：ThreadingHTTPServer + 常驻事件循环线程。"""

import asyncio
import base64
import binascii
import io
import json
import queue
import tempfile
import threading
import time
import wave
from concurrent.futures import Future
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from inputs import speech
from inference.models import DeepSeekModel, LocalModel
from integrations.mcp_client import MCPSession
from shared.config import AssistantConfig
from shared.reporting import log
from .service import AgentService
from shared.contracts import (Message, MessageHeader, VoiceInput, TextInput,
                              EmptyPayload, RecordCommandPayload)
from shared.messages import AGENT, agent_error, agent_output, check_route

MAX_VOICE_BYTES = 8 * 1024 * 1024  # 16k/16bit/单声道约 1.92MB/分钟，8MB ≈ 4 分钟
MAX_TEXT_BYTES = 64 * 1024
CALL_TIMEOUT = 180.0
MAX_VOICE_JSON_BYTES = 4 * ((MAX_VOICE_BYTES + 2) // 3) + MAX_TEXT_BYTES


def check_wav_header(data: bytes) -> None:
    """校验浏览器录音为 16000Hz 单声道 PCM WAV；不符时抛 ValueError（映射 422）。"""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("音频不是有效的 WAV 文件")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(),
                    audio.getcomptype()) != (16000, 1, 2, "NONE"):
                raise ValueError("需要 16000Hz 单声道 PCM16 WAV")
            expected = audio.getnframes() * 2
            if not expected or len(audio.readframes(audio.getnframes())) != expected:
                raise ValueError("WAV 没有音频数据或内容不完整")
    except (wave.Error, EOFError) as exc:
        raise ValueError("音频不是有效的 PCM WAV 文件") from exc


@dataclass
class AgentRuntime:
    loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    service: AgentService
    server: ThreadingHTTPServer
    mcp_session: MCPSession | None = None
    session_task: Future | None = None
    shutdown_event: asyncio.Event | None = None
    _stopped: bool = field(default=False)

    def close(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.shutdown_event is not None:
            self.loop.call_soon_threadsafe(self.shutdown_event.set)
        if self.session_task is not None:
            try:
                self.session_task.result(timeout=10)
            except Exception as exc:  # 退出清理尽力而为
                print(f"[agent 服务] MCP 会话关闭异常：{exc}", flush=True)
                self.session_task.cancel()
        async def cancel_pending():
            pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        asyncio.run_coroutine_threadsafe(cancel_pending(), self.loop).result(timeout=10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()
        self.service.close()
        self.server.server_close()


def build_runtime(config: AssistantConfig, host: str, port: int,
                  asr_model=None, recorder_factory=None) -> AgentRuntime:
    """启动事件循环线程、加载 ASR、建立 MCP 会话并返回监听中的 HTTP 服务。"""
    if asr_model is None:
        log("ASR", "加载本地 Paraformer；服务启动阶段只加载一次")
        asr_model = speech.load_model(config.asr_model_dir, config.asr_hotwords_file)

    if config.deepseek:
        if not config.api_key:
            raise ValueError("使用 DeepSeek 需要设置环境变量 DEEPSEEK_API_KEY")
        log("L1", f"DeepSeek 云端模型：{config.model}")
        model = DeepSeekModel(api_key=config.api_key, model=config.model, url=config.url)
    else:
        log("L1", f"本地 oMLX 模型：{config.model}")
        model = LocalModel(config.model, config.url)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="agent-loop", daemon=True)
    thread.start()
    ready = Future()
    mcp_session = MCPSession(config)

    async def own_session():
        # AnyIO 的 cancel scope 必须在同一个 asyncio Task 内进入和退出。
        # 一个常驻 owner Task 持有会话，HTTP 协程只借用它发起工具请求。
        from assistant.workflow import Assistant
        try:
            async with mcp_session:
                assistant = Assistant(model, mcp_session)
                await assistant.prepare()
                service = AgentService(assistant, asr_model, recorder_factory=recorder_factory,
                                       max_seconds=config.record_max_seconds)
                shutdown = asyncio.Event()
                ready.set_result((service, shutdown))
                await shutdown.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise

    session_task = asyncio.run_coroutine_threadsafe(own_session(), loop)
    service = None
    shutdown = None
    try:
        service, shutdown = ready.result(timeout=60)
        server = AgentHTTPServer((host, port), service, loop=loop)
    except BaseException:
        if shutdown is not None:
            loop.call_soon_threadsafe(shutdown.set)
        else:
            session_task.cancel()
        try:
            session_task.result(timeout=10)
        except BaseException:
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
        if service is not None:
            service.close()
        raise
    return AgentRuntime(loop=loop, thread=thread, service=service, server=server,
                        mcp_session=mcp_session, session_task=session_task,
                        shutdown_event=shutdown)


class AgentHandler(BaseHTTPRequestHandler):
    request_header: MessageHeader | None = None

    def reply(self, status: int, body: dict) -> None:
        if "header" not in body:
            # 语义响应按 payload.type 补 code + message；其余（HTTP 层错误）只留 code + message。
            body = (agent_output(body, request=self.request_header) if "type" in body
                    else agent_error(status, body.get("error", "请求处理失败"),
                                     request=self.request_header))
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if status == 405:
            self.send_header("Allow", "POST")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        self.request_header = None
        self.reply(405, {"error": "请使用 POST 和 header + payload JSON 请求体"})

    def do_POST(self) -> None:
        self.request_header = None
        path = urlsplit(self.path).path
        routes = {
            "/api/voice": ("VOICE_INPUT", VoiceInput),
            "/api/text": ("AGENT_INPUT", TextInput),
            "/api/record": ("RECORD_COMMAND", RecordCommandPayload),
            "/api/record/status": ("RECORD_STATUS", EmptyPayload),
            "/api/state": ("STATE_QUERY", EmptyPayload),
            "/health": ("HEALTH_QUERY", EmptyPayload),
        }
        if path not in routes:
            self.reply(404, {"error": "接口不存在"})
            return
        try:
            msg_type, payload_model = routes[path]
            message = self.read_message(msg_type, payload_model,
                MAX_VOICE_JSON_BYTES if path == "/api/voice" else MAX_TEXT_BYTES)
            if message is None:
                return
            if path == "/api/voice":
                self.handle_voice(message)
            elif path == "/api/record/status":
                status, body = self.submit(self.server.service.handle_record_status(message.header))
                self.reply(status, body)
            elif path == "/api/record":
                if message.payload.type == "record_start":
                    status, body = self.submit(
                        self.server.service.handle_record_start(message.header))
                    self.reply(status, body)
                else:
                    self.handle_record_stop(message)
            elif path == "/api/text":
                status, body = self.submit(self.server.service.handle_text(
                    message.header, message.payload.text))
                self.reply(status, body)
            elif path == "/api/state":
                self.reply(200, self.submit(self.server.service.get_state()))
            else:
                self.reply(200, {"type": "health", "status": "ok", "service": "agent-web",
                    "asr_ready": self.server.service.asr_model is not None})
        except TimeoutError:
            # 不取消可能已产生业务副作用的协程；调用方用原编号重试取得缓存回执。
            self.reply(504, {"error":
                "Agent 等待超时，请先查看任务状态；重试时沿用原消息编号。"})
        except OSError as exc:
            self.reply(500, {"error": f"Agent 服务调用失败：{exc}"})

    def read_message(self, msg_type, payload_model, limit):
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type != "application/json":
            self.reply(415, {"error": "需要 application/json 的 header + payload 消息"})
            return None
        data = self.read_body(limit)
        if data is None:
            return None
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("请求体必须是 header + payload JSON 对象")
            # 先校验消息头，后续 payload 错误仍能关联请求并交换 src/dst。
            header = MessageHeader.model_validate(value.get("header"))
            self.request_header = header
            check_route(header, msg_type, "FRONT", AGENT)
            return Message[payload_model].model_validate(value)
        except (ValueError, TypeError) as exc:
            self.reply(400, {"error": f"请求消息无效：{exc}"})
            return None

    def handle_voice(self, message) -> None:
        try:
            data = base64.b64decode(message.payload.audio_base64, validate=True)
            if len(data) > MAX_VOICE_BYTES:
                raise ValueError("解码后的 WAV 超过 8 MiB")
            check_wav_header(data)
        except (ValueError, binascii.Error) as exc:
            self.reply(422, {"error": f"音频无效：{exc}"})
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as file:
            file.write(data)
            wav_path = Path(file.name)
        self.stream_ndjson(
            message,
            lambda on_transcript: self.server.service.handle_voice(
                message.header, wav_path, on_transcript),
            cleanup=lambda: wav_path.unlink(missing_ok=True))

    def handle_record_stop(self, message) -> None:
        self.stream_ndjson(
            message,
            lambda on_transcript: self.server.service.handle_record_stop(
                message.header, on_transcript))

    def stream_ndjson(self, message, run, cleanup=None) -> None:
        """语音与录音共用的分阶段响应：两帧都是 AGENT_OUTPUT，先 payload.type=asr，后本次结果。

        `run(on_transcript)` 返回最终 (status, body)；请求连接关闭后也继续完成
        Agent 流程并缓存结果，不因断线重复执行工具。`cleanup` 在协程结束时执行。
        """
        updates = queue.Queue()

        async def worker():
            try:
                status, body = await run(
                    lambda body: updates.put((200, body, False)))
                updates.put((status, body, True))
            except Exception as exc:
                log("错误", f"语音处理失败：{exc}")
                updates.put((500, agent_error(500, "语音处理失败，请查看任务状态后使用原消息编号重试。",
                                              request=message.header), True))
            finally:
                if cleanup is not None:
                    cleanup()

        asyncio.run_coroutine_threadsafe(worker(), self.server.loop)
        deadline = time.monotonic() + CALL_TIMEOUT
        streaming = False
        try:
            while True:
                try:
                    status, body, done = updates.get(timeout=max(0, deadline - time.monotonic()))
                except queue.Empty:
                    body = agent_error(504, "Agent 等待超时，请先查看任务状态；重试时沿用原消息编号。",
                                       request=message.header)
                    if not streaming:
                        self.reply(504, body)
                        return
                    done = True
                if not streaming:
                    if status != 200:
                        self.reply(status, body)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-store, no-transform")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    streaming = True
                self.wfile.write((json.dumps(body, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                self.wfile.flush()
                if done:
                    return
        except OSError:
            # 浏览器断开不再向同一连接发送错误响应。
            return

    def read_body(self, limit: int) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reply(400, {"error": "Content-Length 无效"})
            return None
        if not 0 < length <= limit:
            self.reply(400, {"error": f"请求体为空或超过 {limit} 字节"})
            return None
        try:
            self.connection.settimeout(CALL_TIMEOUT)
            data = self.rfile.read(length)
            if len(data) != length:
                raise OSError("请求体长度不足")
            return data
        except OSError as exc:
            self.reply(400, {"error": f"请求体读取失败：{exc}"})
            return None

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.server.loop)\
            .result(timeout=CALL_TIMEOUT)

    def log_message(self, fmt: str, *args) -> None:
        if not self.server.quiet:
            print("[agent 服务] " + fmt % args, flush=True)


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: AgentService, *,
                 loop: asyncio.AbstractEventLoop, quiet: bool = False):
        self.service = service
        self.loop = loop
        self.quiet = quiet
        super().__init__(address, AgentHandler)
