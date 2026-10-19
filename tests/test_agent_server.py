"""Verify the agent HTTP service: voice/text endpoints, events, dedup and state."""

import io
import base64
import json
import asyncio
import tempfile
import threading
import unittest
import wave
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from agent.http_server import build_runtime, check_wav_header, MAX_VOICE_BYTES
from shared.contracts import Message, AgentReply
from pydantic import TypeAdapter
from shared.messages import FRONT, AGENT, make_message, next_msg_id
from tests.test_workflow import (EXAMPLE, INVALID_PAYLOADS, VALID, message, openai_stub,
                                 upload_call, workflow_args)

REQUEST_ID = "MSG-550e8400-e29b-41d4-a716-446655440000"


def wav_bytes(frames: int = 1600) -> bytes:
    """构造 16000Hz 单声道 16bit WAV 字节，模拟浏览器录音。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


@contextmanager
def running_agent(task_output_dir: Path, **overrides):
    config = replace(workflow_args(task_output_dir), **overrides)
    runtime = build_runtime(config, "127.0.0.1", 0, asr_model=object())
    worker = threading.Thread(target=runtime.server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{runtime.server.server_port}"
    finally:
        runtime.server.shutdown()
        runtime.close()
        worker.join(timeout=5)


def counting_stub(reply: dict):
    """替换 build_runtime 使用的 LocalModel；propose 调用记录在 calls 列表。"""
    calls = []

    class CountingModel:
        def __init__(self, model: str, url: str):
            self.reply = reply

        async def propose(self, messages: list[dict], tools: list[dict]) -> dict:
            calls.append(messages)
            return self.reply

    return CountingModel, calls


class SequentialModel:
    """第一轮创建任务，第二轮从历史工具结果中提取任务编号并上传。"""

    def __init__(self, model: str, url: str):
        self.rounds = 0

    async def propose(self, messages, tools):
        self.rounds += 1
        if self.rounds == 1:
            return message(VALID)
        tool = next(m for m in messages if m.get("role") == "tool")
        task_id = json.loads(tool["content"])["task_id"]
        return upload_call(task_id) if self.rounds == 2 else {
            "tool_calls": [{"function": {"name": "execute_task",
                                          "arguments": json.dumps({"task_id": task_id})}}]}


MSG_TYPES = {"/api/voice": "VOICE_INPUT", "/api/text": "AGENT_INPUT",
             "/api/state": "STATE_QUERY", "/health": "HEALTH_QUERY"}


def post(base, path, payload, msg_id=None, ref_msg_id=None, **overrides):
    body = make_message(MSG_TYPES[path], payload, src=FRONT, dst=AGENT,
                        msg_id=msg_id, ref_msg_id=ref_msg_id)
    response = httpx.post(base + path, json=body, trust_env=False, timeout=60, **overrides)
    if response.headers.get("content-type", "").startswith("application/x-ndjson"):
        frames = [json.loads(line) for line in response.iter_lines() if line]
        assert len(frames) == 2
        assert [frame["header"]["msg_type"] for frame in frames] == ["ASR_OUTPUT", "AGENT_OUTPUT"]
        for frame in frames:
            Message[dict].model_validate(frame)
            assert frame["header"]["ref_msg_id"] == body["header"]["msg_id"]
        response = httpx.Response(response.status_code, json=frames[-1],
                                  extensions={"voice_frames": frames})
    return response


def voice_payload(audio=None):
    return {"audio_format": "wav", "audio_base64": base64.b64encode(
        wav_bytes() if audio is None else audio).decode("ascii")}


class AgentServerTests(unittest.TestCase):
    def assert_message(self, response, msg_type, ref=None, status=200):
        self.assertEqual(response.status_code, status, response.text)
        body = Message[dict].model_validate(response.json())
        self.assertEqual(set(response.json()), {"header", "payload"})
        self.assertEqual(body.header.msg_type, msg_type)
        self.assertEqual(body.header.src.model_dump(), AGENT)
        self.assertEqual(body.header.dst.model_dump(), FRONT)
        self.assertEqual(body.header.ref_msg_id, ref)
        return body.payload

    def test_state_restores_all_three_stages_without_model_call(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent.http_server.LocalModel", SequentialModel), running_agent(Path(directory)) as base:
            initial = post(base, "/api/state", {}).json()["payload"]
            self.assertEqual(initial["tasks"], [])
            snapshots = []
            for command in (EXAMPLE, "上传任务", "执行任务"):
                msg_id = next_msg_id()
                response = post(base, "/api/text", {"text": command}, msg_id)
                result = self.assert_message(response, "AGENT_OUTPUT", msg_id)
                TypeAdapter(AgentReply).validate_python(result)
                self.assertEqual(result["task"], VALID["task"])
                self.assertEqual(set(result), {"type", "tool", "task_id", "status", "task"})
                snapshots.append(post(base, "/api/state", {}).json()["payload"])
            restored = post(base, "/api/state", {}).json()["payload"]
            self.assertEqual(restored, snapshots[-1])
            self.assertEqual(restored["session_id"], initial["session_id"])
            self.assertEqual(restored["tasks"][0]["status"], "executing")
            self.assertEqual(restored["tasks"][0]["task"], VALID["task"])
            self.assertEqual([change["after"]["status"] for change in restored["changes"]],
                             ["draft", "uploaded", "executing"])
            self.assertIsNone(restored["changes"][0]["before"])
            self.assertEqual(restored["changes"][1]["before"]["status"], "draft")
            self.assertEqual(snapshots[0]["tasks"][0]["status"], "draft")

    def test_simultaneous_replay_calls_model_once(self):
        Model, calls = counting_stub(message(VALID))
        original = Model.propose
        async def slow_propose(model, messages, tools):
            await asyncio.sleep(0.05)
            return await original(model, messages, tools)
        Model.propose = slow_propose
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent.http_server.LocalModel", Model), running_agent(Path(directory)) as base:
            def send(_):
                return post(base, "/api/text", {"text": EXAMPLE}, REQUEST_ID).json()
            with ThreadPoolExecutor(max_workers=3) as pool:
                responses = list(pool.map(send, range(3)))
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(body == responses[0] for body in responses))
            state = post(base, "/api/state", {}).json()["payload"]
            self.assertEqual(len(state["tasks"]), 1)
            self.assertEqual(len(state["changes"]), 1)

    def test_voice_runs_agent_once_without_frontend_text_resubmit(self):
        Model, calls = counting_stub(message(VALID))
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent.http_server.LocalModel", Model), running_agent(Path(directory)) as base, \
                patch("inputs.speech.transcribe_file", return_value=EXAMPLE) as transcribe:
            first = post(base, "/api/voice", voice_payload(), REQUEST_ID)
            body = self.assert_message(first, "AGENT_OUTPUT", REQUEST_ID)
            self.assertEqual(first.extensions["voice_frames"][0]["payload"], {"text": EXAMPLE})
            self.assertEqual(body["tool"], "create_task")
            self.assertEqual(body["type"], "task_result")
            self.assertEqual(body["task"], VALID["task"])
            TypeAdapter(AgentReply).validate_python(body)
            replay = post(base, "/api/voice", voice_payload(), REQUEST_ID)
            self.assertEqual(replay.json(), first.json())
            self.assertEqual(replay.extensions["voice_frames"], first.extensions["voice_frames"])
            self.assertEqual(transcribe.call_count, 1)
            self.assertEqual(len(calls), 1)
            state = post(base, "/api/state", {}).json()["payload"]
            self.assertEqual(len(state["tasks"]), 1)
            self.assertEqual(state["changes"][0]["ref_msg_id"], REQUEST_ID)
            # 独立的文字口令与语音编号即使相同，也不混淆缓存。
            text = post(base, "/api/text", {"text": EXAMPLE}, REQUEST_ID)
            self.assertNotIn("transcript", text.json()["payload"])
            self.assertEqual(len(calls), 2)

    def test_simultaneous_voice_replay_does_not_repeat_asr_or_create(self):
        Model, calls = counting_stub(message(VALID))
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent.http_server.LocalModel", Model), running_agent(Path(directory)) as base, \
                patch("inputs.speech.transcribe_file", return_value=EXAMPLE) as transcribe:
            def send(_):
                return post(base, "/api/voice", voice_payload(), REQUEST_ID).json()
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(send, range(3)))
            self.assertTrue(all(result == results[0] for result in results))
            self.assertEqual(transcribe.call_count, 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(post(base, "/api/state", {}).json()["payload"]["tasks"]), 1)

    def test_transcript_arrives_while_llm_is_still_blocked(self):
        release = threading.Event()
        entered = threading.Event()
        Model, calls = counting_stub(message(VALID))
        original = Model.propose

        async def blocked_propose(model, messages, tools):
            entered.set()
            if not await asyncio.to_thread(release.wait, 5):
                raise TimeoutError("test did not release model")
            return await original(model, messages, tools)

        Model.propose = blocked_propose
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent.http_server.LocalModel", Model), running_agent(Path(directory)) as base, \
                patch("inputs.speech.transcribe_file", return_value=EXAMPLE):
            request = make_message("VOICE_INPUT", voice_payload(), src=FRONT, dst=AGENT, msg_id=REQUEST_ID)
            try:
                with httpx.stream("POST", base + "/api/voice", json=request,
                                  trust_env=False, timeout=3) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.headers["content-type"].startswith("application/x-ndjson"))
                    lines = response.iter_lines()
                    transcript = Message[dict].model_validate(json.loads(next(lines)))
                    self.assertEqual(transcript.header.msg_type, "ASR_OUTPUT")
                    self.assertEqual(transcript.header.ref_msg_id, REQUEST_ID)
                    self.assertEqual(transcript.payload, {"text": EXAMPLE})
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(calls, [])  # 模型尚未返回，HTTP 已交付转写。
                    self.assertEqual(post(base, "/api/state", {}).json()["payload"]["tasks"], [])
                    release.set()
                    result = Message[AgentReply].model_validate(json.loads(next(lines)))
                    self.assertEqual(result.header.msg_type, "AGENT_OUTPUT")
                    self.assertEqual(result.header.ref_msg_id, REQUEST_ID)
                    self.assertEqual(result.payload.status, "draft")
                    self.assertEqual(list(lines), [])
                self.assertEqual(len(calls), 1)
            finally:
                release.set()

    def test_disconnect_after_transcript_then_retry_does_not_repeat_processing(self):
        release = threading.Event()
        entered = threading.Event()
        Model, calls = counting_stub(message(VALID))
        original = Model.propose

        async def blocked_propose(model, messages, tools):
            entered.set()
            await asyncio.to_thread(release.wait, 5)
            return await original(model, messages, tools)

        Model.propose = blocked_propose
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent.http_server.LocalModel", Model), running_agent(Path(directory)) as base, \
                patch("inputs.speech.transcribe_file", return_value=EXAMPLE) as transcribe:
            request = make_message("VOICE_INPUT", voice_payload(), src=FRONT, dst=AGENT, msg_id=REQUEST_ID)
            try:
                with httpx.stream("POST", base + "/api/voice", json=request,
                                  trust_env=False, timeout=3) as response:
                    first = json.loads(next(response.iter_lines()))
                self.assertTrue(entered.wait(1))
                with ThreadPoolExecutor(max_workers=1) as pool:
                    replay = pool.submit(post, base, "/api/voice", voice_payload(), REQUEST_ID)
                    release.set()
                    result = replay.result(timeout=5)
                self.assertEqual(result.extensions["voice_frames"][0], first)
                self.assertEqual(result.json()["payload"]["status"], "draft")
                self.assertEqual(transcribe.call_count, 1)
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(post(base, "/api/state", {}).json()["payload"]["tasks"]), 1)
            finally:
                release.set()

    def test_voice_tool_failure_preserves_transcript_without_leaking_arguments(self):
        Model, calls = counting_stub(upload_call("TASK-" + "0" * 32))
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent.http_server.LocalModel", Model), running_agent(Path(directory)) as base, \
                patch("inputs.speech.transcribe_file", return_value="上传晨曦任务"):
            response = post(base, "/api/voice", voice_payload(), REQUEST_ID)
            body = self.assert_message(response, "AGENT_OUTPUT", REQUEST_ID)
            self.assertEqual(body["type"], "error")
            self.assertEqual(body["tool"], "upload_task")
            self.assertEqual(response.extensions["voice_frames"][0]["payload"], {"text": "上传晨曦任务"})
            self.assertEqual(set(body), {"type", "text", "tool"})
            TypeAdapter(AgentReply).validate_python(body)
            self.assertEqual(len(calls), 1)
            self.assertEqual(post(base, "/api/state", {}).json()["payload"]["tasks"], [])

    def test_text_requires_envelope_not_bare_json(self):
        with tempfile.TemporaryDirectory() as directory, running_agent(Path(directory)) as base:
            for payload in (None, [], "text", 42, {"text": "hi"}, {"payload": {"text": "hi"}}):
                response = httpx.post(base + "/api/text", content=json.dumps(payload),
                                      headers={"Content-Type": "application/json"}, trust_env=False)
                self.assert_message(response, "ERROR", status=400)

    def test_header_validates_uuid_version_type_and_route(self):
        with tempfile.TemporaryDirectory() as directory, running_agent(Path(directory)) as base:
            for changes in (
                {"msg_id": "AGENTMSG-20261016-000001"}, {"msg_id": "MSG-not-a-uuid"},
                {"version": "2.0.0"}, {"msg_type": "VOICE_INPUT"},
                {"src": AGENT}, {"dst": FRONT}, {"ref_msg_id": "bad"},
            ):
                with self.subTest(changes=changes):
                    request = make_message("AGENT_INPUT", {"text": EXAMPLE}, src=FRONT, dst=AGENT)
                    request["header"].update(changes)
                    response = httpx.post(base + "/api/text", json=request, trust_env=False)
                    self.assertEqual(response.status_code, 400)
                    Message[dict].model_validate(response.json())
                    self.assertEqual(response.json()["header"]["msg_type"], "ERROR")
                    self.assertEqual(response.json()["header"]["src"], AGENT)
            self.assertEqual(post(base, "/api/state", {}).json()["payload"]["tasks"], [])

    def test_wav_requires_pcm16_and_complete_audio(self):
        check_wav_header(wav_bytes())
        for invalid in (wav_bytes()[:-2], wav_bytes(0)):
            with self.assertRaises(ValueError):
                check_wav_header(invalid)
        data = bytearray(wav_bytes())
        data[34:36] = (8).to_bytes(2, "little")
        with self.assertRaises(ValueError):
            check_wav_header(bytes(data))

    def test_queries_are_post_with_envelopes(self):
        with tempfile.TemporaryDirectory() as directory, running_agent(Path(directory)) as base:
            for path, msg_type in (("/health", "HEALTH_OUTPUT"), ("/api/state", "STATE_OUTPUT")):
                msg_id = next_msg_id()
                body = self.assert_message(post(base, path, {}, msg_id), msg_type, msg_id)
                if path == "/health":
                    self.assertEqual(body, {"status": "ok", "service": "agent-web", "asr_ready": True})
                self.assert_message(httpx.get(base + path, trust_env=False), "ERROR", status=405)
                response = post(base, path, {"extra": True}, msg_id)
                self.assert_message(response, "ERROR", msg_id, status=400)

    def test_voice_clarifies_without_tool_and_preserves_transcript(self):
        Model, calls = counting_stub({"content": "请补充起飞高度。"})
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent.http_server.LocalModel", Model), running_agent(Path(directory)) as base, \
                patch("inputs.speech.transcribe_file", return_value=EXAMPLE):
            response = post(base, "/api/voice", voice_payload(), REQUEST_ID)
            body = self.assert_message(response, "AGENT_OUTPUT", REQUEST_ID)
            self.assertEqual(body, {"type": "clarify", "text": "请补充起飞高度。"})
            self.assertEqual(response.extensions["voice_frames"][0]["payload"], {"text": EXAMPLE})
            self.assertEqual(len(calls), 1)
            state = post(base, "/api/state", {}).json()["payload"]
            self.assertEqual(state["tasks"], [])
            self.assertEqual(state["pending"], body["text"])

    def test_voice_rejects_invalid_base64_wav_or_decoded_size(self):
        with tempfile.TemporaryDirectory() as directory, running_agent(Path(directory)) as base, \
                patch("inputs.speech.transcribe_file") as transcribe:
            for payload in (
                {"audio_format": "wav", "audio_base64": "not base64!"},
                voice_payload(b"not-a-wav-file" * 4), voice_payload(wav_bytes()[:-2]),
                voice_payload(b"x" * (MAX_VOICE_BYTES + 1)),
            ):
                response = post(base, "/api/voice", payload, REQUEST_ID)
                self.assert_message(response, "ERROR", REQUEST_ID, status=422)
            for payload in (voice_payload(b""), {"audio_format": "mp3", "audio_base64": "eA=="}):
                self.assert_message(post(base, "/api/voice", payload, REQUEST_ID),
                                    "ERROR", REQUEST_ID, status=400)
            transcribe.assert_not_called()

    def test_asr_empty_or_failure_still_returns_error_envelope(self):
        with tempfile.TemporaryDirectory() as directory, running_agent(Path(directory)) as base:
            for result, status in (("", 422), (RuntimeError("ASR unavailable"), 500)):
                with self.subTest(result=result), patch("inputs.speech.transcribe_file") as transcribe:
                    if isinstance(result, Exception):
                        transcribe.side_effect = result
                    else:
                        transcribe.return_value = result
                    msg_id = next_msg_id()
                    self.assert_message(post(base, "/api/voice", voice_payload(), msg_id),
                                        "ERROR", msg_id, status=status)

    def test_text_returns_only_current_task_result(self):
        with tempfile.TemporaryDirectory() as directory, openai_stub(message(VALID)) as model_url, \
                running_agent(Path(directory), url=model_url) as base:
            body = self.assert_message(post(base, "/api/text", {"text": EXAMPLE}, REQUEST_ID),
                                       "AGENT_OUTPUT", REQUEST_ID)
            self.assertEqual(set(body), {"type", "tool", "task_id", "status", "task"})
            self.assertEqual(body["type"], "task_result")
            self.assertEqual(body["tool"], "create_task")
            self.assertEqual(body["status"], "draft")
            self.assertRegex(body["task_id"], r"^TASK-[0-9a-f]{32}$")
            self.assertEqual(body["task"], VALID["task"])
            state = post(base, "/api/state", {}).json()["payload"]
            self.assertEqual(state["tasks"], [{"task_id": body["task_id"], "status": "draft", "task": VALID["task"]}])

    def test_text_clarify_event(self):
        with tempfile.TemporaryDirectory() as directory, openai_stub({"content": "请问起飞高度是多少？"}) as url, \
                running_agent(Path(directory), url=url) as base:
            body = post(base, "/api/text", {"text": "新增原地起飞任务"}).json()["payload"]
            self.assertEqual(body, {"type": "clarify", "text": "请问起飞高度是多少？"})
            state = post(base, "/api/state", {}).json()["payload"]
            self.assertEqual(state["pending"], body["text"])

    def test_text_error_event_on_tool_failure(self):
        with tempfile.TemporaryDirectory() as directory, openai_stub(message(INVALID_PAYLOADS[7])) as url, \
                running_agent(Path(directory), url=url) as base:
            response = post(base, "/api/text", {"text": EXAMPLE})
            self.assertEqual(response.status_code, 200)
            body = response.json()["payload"]
            self.assertEqual(body["type"], "error")
            self.assertEqual(set(body), {"type", "text"})
            self.assertEqual(post(base, "/api/state", {}).json()["payload"]["tasks"], [])

    def test_dedup_returns_same_envelope_and_skips_llm(self):
        Model, calls = counting_stub(message(VALID))
        with tempfile.TemporaryDirectory() as directory, patch("agent.http_server.LocalModel", Model), \
                running_agent(Path(directory)) as base:
            responses = [post(base, "/api/text", {"text": EXAMPLE}, REQUEST_ID) for _ in range(2)]
            self.assertEqual(responses[0].json(), responses[1].json())
            self.assertEqual(len(calls), 1)
            self.assertEqual(responses[0].json()["payload"]["status"], "draft")

    def test_full_create_then_upload_chain(self):
        with tempfile.TemporaryDirectory() as directory, patch("agent.http_server.LocalModel", SequentialModel), \
                running_agent(Path(directory)) as base:
            created = post(base, "/api/text", {"text": EXAMPLE}).json()["payload"]
            uploaded = post(base, "/api/text", {"text": "上传任务"}).json()["payload"]
            before, after = created, uploaded
            self.assertEqual(before["status"], "draft")
            self.assertEqual(after["status"], "uploaded")
            self.assertEqual(after["task_id"], before["task_id"])
            self.assertNotIn("file", uploaded)
            state = post(base, "/api/state", {}).json()["payload"]
            self.assertEqual(state["tasks"][0]["status"], "uploaded")
            files = list(Path(directory).glob("TASK-*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text())["task"], VALID["task"])

    def test_content_type_json_payload_and_paths_are_checked(self):
        with tempfile.TemporaryDirectory() as directory, running_agent(Path(directory)) as base:
            self.assert_message(httpx.post(base + "/api/voice", content=wav_bytes(),
                headers={"Content-Type": "audio/wav"}, trust_env=False), "ERROR", status=415)
            self.assert_message(httpx.post(base + "/api/text", content=b"{",
                headers={"Content-Type": "application/json"}, trust_env=False), "ERROR", status=400)
            self.assert_message(httpx.post(base + "/api/text", content=b"",
                headers={"Content-Type": "application/json"}, trust_env=False), "ERROR", status=400)
            for payload in ({"text": "  "}, {"text": EXAMPLE, "ref_msg_id": REQUEST_ID},
                            {"text": 123}):
                self.assert_message(post(base, "/api/text", payload, REQUEST_ID),
                                    "ERROR", REQUEST_ID, status=400)
            self.assert_message(httpx.post(base + "/missing", json={}, trust_env=False),
                                "ERROR", status=404)

    def test_response_routes_back_to_requesting_station(self):
        with tempfile.TemporaryDirectory() as directory, running_agent(Path(directory)) as base:
            request = make_message("HEALTH_QUERY", {}, src={"type": "FRONT", "id": "GS-02"}, dst=AGENT)
            response = httpx.post(base + "/health", json=request, trust_env=False).json()
            self.assertEqual(response["header"]["dst"], request["header"]["src"])
            self.assertEqual(response["header"]["ref_msg_id"], request["header"]["msg_id"])


if __name__ == "__main__":
    unittest.main()
