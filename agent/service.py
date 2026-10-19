"""Agent 服务核心：把 ASR 与 Assistant 工作流包装为可被 HTTP 调用的协程。

线程约定（不变量）：handle_voice / handle_text 两个协程只运行在常驻事件循环
线程上，因此响应组装、msg_id 生成、去重缓存与会话任务
列表的读写都无需加锁；阻塞的 funasr 转写通过单工作线程 executor 派发，避免
阻塞事件循环，同时天然串行化对非线程安全模型的调用。
"""

import asyncio
from copy import deepcopy
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from inputs import speech
from shared.contracts import (MessageHeader, AgentTaskReply, AgentClarifyReply,
                              AgentErrorReply)
from shared.reporting import error_details, log
from shared.messages import make_message

DEDUP_CAPACITY = 256


class AgentService:
    def __init__(self, assistant, asr_model):
        self.assistant = assistant
        self.asr_model = asr_model
        # funasr 模型假设非线程安全且调用阻塞：单工作线程 = 天然互斥 + 不卡事件循环。
        self.asr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
        self.chat_lock = asyncio.Lock()  # Assistant.history/pending 不是并发安全的
        self.voice_lock = asyncio.Lock()
        self._seen: OrderedDict[str, tuple[int, dict]] = OrderedDict()
        self._transcripts: dict[str, dict] = {}
        self._tasks: OrderedDict[str, dict] = OrderedDict()  # task_id -> 面板数据
        self._changes: list[dict] = []
        self.session_id = uuid4().hex
        self.revision = 0

    # ---- 请求入口（协程，仅 loop 线程） ----

    async def handle_voice(self, header: MessageHeader, wav_path: Path,
                           on_transcript=None) -> tuple[int, dict]:
        # 去重检查也要位于锁内，防止并发重发在首个响应生成前重复执行。
        async with self.voice_lock:
            return await self._handle_voice(header, wav_path, on_transcript)

    async def _handle_voice(self, header: MessageHeader, wav_path: Path,
                            on_transcript) -> tuple[int, dict]:
        request_msg_id = f"voice:{header.src.id}:{header.msg_id}"
        cached = self._dedup_get(request_msg_id)
        if cached is not None:
            if on_transcript is not None and request_msg_id in self._transcripts:
                on_transcript(self._transcripts[request_msg_id])
            return cached
        try:
            task_names = tuple(dict.fromkeys(task["task_name"]
                for task in self.assistant.known_tasks.values() if task.get("task_name")))
            text = await asyncio.get_running_loop().run_in_executor(
                self.asr_pool, lambda: speech.transcribe_file(
                    self.asr_model, wav_path, task_names=task_names))
        except Exception as exc:
            log("ASR", "转写失败：" + error_details(exc))
            return self._dedup_put(request_msg_id, 500, make_message("ERROR", {"error": f"语音识别失败：{exc}"}, request=header))
        if not text:
            return self._dedup_put(request_msg_id, 422,
                                   make_message("ERROR", {"error": "没有识别到文本，请重试"}, request=header))
        log("ASR", "转写：" + text)
        transcript = make_message("ASR_OUTPUT", {"text": text}, request=header)
        self._transcripts[request_msg_id] = transcript
        if on_transcript is not None:
            on_transcript(transcript)
        # ASR 文本直接交给同一 Agent，不经浏览器重新提交。
        async with self.chat_lock:
            payload = await self._process_text(header, text)
        body = make_message("AGENT_OUTPUT", payload, request=header)
        return self._dedup_put(request_msg_id, 200, body)

    async def handle_text(self, header: MessageHeader, text: str) -> tuple[int, dict]:
        async with self.chat_lock:
            cache_key = f"text:{header.src.id}:{header.msg_id}"
            cached = self._dedup_get(cache_key)
            if cached is not None:
                return cached
            payload = await self._process_text(header, text)
            body = make_message("AGENT_OUTPUT", payload, request=header)
            return self._dedup_put(cache_key, 200, body)

    async def _process_text(self, header: MessageHeader, text: str) -> dict:
        if header.ref_msg_id is not None:
            log("上下文", f"本次输入关联消息：{header.ref_msg_id}")
        try:
            await self.assistant.process_text(text)
            events = list(self.assistant.last_events)
        except Exception as exc:
            log("错误", error_details(exc))
            events = [*self.assistant.last_events, {"type": "error", "text": str(exc)}]
        self.revision += 1
        self._collect_tasks(events, header.msg_id)
        # 内部事件仍用于状态维护；对外只返回一个本次结果，不携带调试参数或快照。
        failure = next((event for event in reversed(events) if event["type"] == "error"), None)
        if failure is not None:
            call = next((event for event in events if event["type"] == "tool_call"), {})
            reply = AgentErrorReply(type="error", text=failure["text"], tool=call.get("name"))
        else:
            result = next((event for event in reversed(events) if event["type"] == "task_result"), None)
            if result is not None:
                entry = self._tasks[result["task_id"]]
                reply = AgentTaskReply(type="task_result", tool=result["tool"], **entry)
            else:
                clarify = next((event for event in events if event["type"] == "clarify"), {})
                reply = AgentClarifyReply(type="clarify", text=clarify.get("text", "请补充任务口令。"))
        return reply.model_dump(exclude_none=True)

    async def get_state(self) -> dict:
        """在 loop 线程取快照；读取不触发模型，也不会重做任何工具调用。"""
        return self._snapshot()

    def _snapshot(self) -> dict:
        return deepcopy({"session_id": self.session_id, "revision": self.revision,
                         "tasks": list(self._tasks.values()), "changes": self._changes,
                         "last_task": self.assistant.last_task,
                         "pending": self.assistant.pending})

    def close(self) -> None:
        self.asr_pool.shutdown(wait=False)

    # ---- 内部工具 ----

    def _collect_tasks(self, events: list[dict], request_msg_id: str) -> None:
        for event in events:
            if event.get("type") != "task_result":
                continue
            entry = {"task_id": event["task_id"], "status": event["status"]}
            task = event.get("task")
            if task is not None:
                entry["task"] = task
            before = self._tasks.get(event["task_id"])
            after = {**(before or {}), **entry}
            if before == after:
                continue
            self.revision += 1
            self._changes.append({"revision": self.revision,
                                  "at": datetime.now(timezone.utc).isoformat(),
                                  "task_id": event["task_id"], "tool": event.get("tool"),
                                  "ref_msg_id": request_msg_id,
                                  "before": deepcopy(before), "after": deepcopy(after)})
            self._changes = self._changes[-100:]
            self._tasks[event["task_id"]] = after

    def _dedup_get(self, request_msg_id: str) -> tuple[int, dict] | None:
        response = self._seen.get(request_msg_id)
        if response is not None:
            self._seen.move_to_end(request_msg_id)
        return response

    def _dedup_put(self, request_msg_id: str, status: int, body: dict) -> tuple[int, dict]:
        self._seen[request_msg_id] = (status, body)
        while len(self._seen) > DEDUP_CAPACITY:
            expired, _ = self._seen.popitem(last=False)
            self._transcripts.pop(expired, None)
        return status, body
