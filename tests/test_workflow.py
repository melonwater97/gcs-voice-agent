"""Verify five task variants, real MCP dispatch, and rejection without writes."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from simulator.backend import SimulatedGCS
from shared.contracts import CreateTaskPayload, INTERFACE_FILE
from shared.config import PROJECT_DIR, AssistantConfig
from inputs.sources import input_texts
from assistant.workflow import run
from assistant.tools import DEFAULT_TOOLS
from simulator.http_server import GCSHTTPServer
from integrations.gcs_http import GCSHTTPClient, build_message
from shared.config import DEFAULT_MODEL, DEFAULT_URL


def pre_launch(mode: str, **parameters) -> dict:
    return {"action": "create_task", "task": {
        "task_type": "放飞前出任务", "parameters": {"mode": mode, **parameters},
    }}


def formation(kind: str) -> dict:
    return {"action": "create_task", "task": {
        "task_type": "编队飞行任务", "parameters": {"formation_type": kind},
    }}


EXAMPLE = "新增放飞前出任务，模式为原地起飞"
VALID = pre_launch("原地起飞")
VARIANTS = [
    pre_launch("原地起飞"),
    pre_launch("在指定位置展开", position={"longitude": 120.1, "latitude": 30.2}),
    pre_launch("在指定区域展开", area={"vertices": [
        {"longitude": 120.1, "latitude": 30.2},
        {"longitude": 120.2, "latitude": 30.2},
        {"longitude": 120.2, "latitude": 30.3},
    ]}),
    formation("C型"),
    formation("U型"),
]
INVALID_PAYLOADS = [
    pre_launch("未知模式"),
    pre_launch("在指定位置展开"),
    pre_launch("在指定位置展开", position={"longitude": 181.0, "latitude": 30.0}),
    pre_launch("在指定区域展开", area={"vertices": [
        {"longitude": 120.0, "latitude": 30.0},
        {"longitude": 121.0, "latitude": 30.0},
    ]}),
    pre_launch("在指定区域展开", area={"vertices": [
        {"longitude": 120.0, "latitude": 30.0},
        {"longitude": 120.0, "latitude": 30.0},
        {"longitude": 121.0, "latitude": 31.0},
    ]}),
    pre_launch("原地起飞", formation_type="C型"),
    formation("V型"),
    {"action": "create_task", "task": {"task_type": "编队飞行任务", "parameters": {}}},
    {"action": "create_task", "task": {"task_type": "未知任务", "parameters": {}}},
    {**VARIANTS[0], "action": "start_flight"},
]


def message(payload: dict, name: str = "create_task") -> dict:
    return {"tool_calls": [{"function": {
        "name": name, "arguments": json.dumps({"payload": payload}, ensure_ascii=False),
    }}]}


def validate_call(reply, allowed):
    invocation = DEFAULT_TOOLS.parse(reply, allowed)
    return invocation.arguments if invocation else None


def stub_local(reply: dict):
    """Replace LocalModel inside run(); every proposal returns the same reply."""

    class StubLocalModel:
        def __init__(self, model: str, url: str):
            self.reply = reply

        async def propose(self, messages: list[dict], tools: list[dict]) -> dict:
            return self.reply

    return StubLocalModel


@contextmanager
def receiving_gcs(directory: str):
    server = GCSHTTPServer(("127.0.0.1", 0), Path(directory), quiet=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@contextmanager
def openai_stub(reply: dict):
    """Minimal OpenAI-compatible chat endpoint so CLI tests avoid a real oMLX."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            body = json.dumps({"choices": [{"message": reply}]},
                              ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def workflow_args(url: str, **overrides) -> AssistantConfig:
    return AssistantConfig(**{
        "text": None, "file": None, "mic": False,
        "model": DEFAULT_MODEL, "url": DEFAULT_URL, "gcs_url": url,
        "asr_model_dir": PROJECT_DIR / "models" / "paraformer-large",
        **overrides,
    })


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_dispatch_five_variants_and_refusal(self):
        with tempfile.TemporaryDirectory(prefix="gcs-layers-test-") as directory, receiving_gcs(directory) as (url, _):
            params = StdioServerParameters(command=sys.executable, args=["-m", "integrations.mcp_server"], cwd=str(PROJECT_DIR),
                env={**os.environ, "GCS_HTTP_URL": url})
            async with Client(params) as client:
                tools = {tool.name for tool in (await client.list_tools()).tools}
                self.assertEqual(tools, {"create_task"})
                for payload in INVALID_PAYLOADS:
                    result = await client.call_tool("create_task", {"payload": payload})
                    self.assertTrue(result.is_error)
                self.assertTrue((await client.call_tool("start_flight", {})).is_error)
                self.assertEqual(list(Path(directory).iterdir()), [])
                original_interface = INTERFACE_FILE.read_bytes()
                task_ids = set()
                for payload in VARIANTS:
                    with self.subTest(payload=payload):
                        command = validate_call(message(payload), tools)
                        self.assertEqual(command.payload.model_dump(), payload)
                        result = await client.call_tool("create_task", command.model_dump())
                        self.assertFalse(result.is_error)
                        task = result.structured_content
                        self.assertEqual(task["status"], "created")
                        self.assertEqual(task["message"]["payload"], payload)
                        self.assertEqual(json.loads(Path(task["file"]).read_text()), task["message"])
                        template_header = json.loads(original_interface)["header"]
                        actual_header = task["message"]["header"]
                        self.assertNotEqual(actual_header["msg_id"], template_header["msg_id"])
                        self.assertEqual({k: v for k, v in actual_header.items() if k != "msg_id"},
                                         {k: v for k, v in template_header.items() if k != "msg_id"})
                        task_ids.add(task["task_id"])
                self.assertEqual(len(task_ids), 5)
                self.assertEqual(len(list(Path(directory).glob("*.json"))), 5)
                self.assertEqual(INTERFACE_FILE.read_bytes(), original_interface)

    def test_l2_rejects_unknown_tools_and_invalid_payloads(self):
        with self.assertRaises(ValueError):
            validate_call(message(VARIANTS[0], "start_flight"), {"create_task", "start_flight"})
        for payload in INVALID_PAYLOADS:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_call(message(payload), {"create_task"})
        self.assertIsNone(validate_call({"content": "需要澄清"}, {"create_task"}))

    def test_l2_rejects_extra_arguments_and_multiple_calls(self):
        invalid = message(VARIANTS[0])
        invalid["tool_calls"][0]["function"]["arguments"] = json.dumps(
            {"payload": VARIANTS[0], "header": {"msg_type": "START_FLIGHT"}})
        with self.assertRaises(ValueError):
            validate_call(invalid, {"create_task"})
        duplicate = message(VARIANTS[0])
        duplicate["tool_calls"] *= 2
        with self.assertRaises(ValueError):
            validate_call(duplicate, {"create_task"})

    def test_backend_rejects_invalid_input_without_writes(self):
        with tempfile.TemporaryDirectory(prefix="gcs-layers-backend-test-") as directory:
            backend = SimulatedGCS(Path(directory))
            for payload in INVALID_PAYLOADS:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    template = json.loads(INTERFACE_FILE.read_text(encoding="utf-8"))
                    backend.create_task({"header": template["header"], "payload": payload})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_interface_payload_matches_default_example(self):
        interface = json.loads(INTERFACE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(set(interface), {"header", "payload"})
        self.assertEqual(CreateTaskPayload.model_validate(interface["payload"]).model_dump(), VARIANTS[0])

    async def test_http_receives_full_json_and_rejects_bad_requests(self):
        with tempfile.TemporaryDirectory(prefix="gcs-http-test-") as directory, receiving_gcs(directory) as (url, _):
            async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
                self.assertEqual((await client.get("/health")).json()["status"], "ok")
                malformed = await client.post("/tasks", content="{", headers={"Content-Type": "application/json"})
                self.assertEqual(malformed.status_code, 400)
                self.assertEqual((await client.post("/tasks", json={"payload": VARIANTS[0]})).status_code, 422)
                invalid = build_message(VARIANTS[0]).model_dump()
                invalid["payload"] = formation("V型")
                self.assertEqual((await client.post("/tasks", json=invalid)).status_code, 422)
                self.assertEqual(list(Path(directory).iterdir()), [])
                request = build_message(VARIANTS[0]).model_dump()
                result = await client.post("/tasks", json=request)
                self.assertEqual(result.status_code, 201)
                self.assertEqual(result.json()["message"], request)

    async def test_asr_file_transcript_flows_through_mcp_and_http(self):
        with tempfile.TemporaryDirectory(prefix="gcs-asr-file-test-") as directory, receiving_gcs(directory) as (url, _):
            args = workflow_args(url, file=Path("recording.wav"))
            asr_model = object()
            with patch("assistant.workflow.LocalModel", stub_local(message(VARIANTS[0]))), \
                 patch("inputs.speech.load_model", return_value=asr_model) as load, \
                 patch("inputs.speech.transcribe_file", return_value="新增放飞前出任务模式为原地起飞") as transcribe, \
                 redirect_stdout(StringIO()) as output:
                await run(args)
            load.assert_called_once_with(args.asr_model_dir)
            transcribe.assert_called_once_with(asr_model, args.file)
            files = list(Path(directory).glob("TASK-*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text())["payload"], VARIANTS[0])
            self.assertIn("已新增放飞前出任务", output.getvalue())

    async def test_microphone_session_reuses_asr_for_multiple_commands(self):
        with tempfile.TemporaryDirectory(prefix="gcs-asr-mic-test-") as directory, receiving_gcs(directory) as (url, _):
            args = workflow_args(url, mic=True)
            with patch("assistant.workflow.LocalModel", stub_local(message(formation("U型")))), \
                 patch("inputs.speech.load_model", return_value=object()) as load, \
                 patch("inputs.speech.iter_microphone_transcripts", return_value=(text for text in [
                     "", EXAMPLE, "新增U型编队飞行任务"])) as recordings, \
                 redirect_stdout(StringIO()) as output:
                await run(args)
            load.assert_called_once()
            recordings.assert_called_once()
            self.assertEqual(len(list(Path(directory).glob("TASK-*.json"))), 2)
            self.assertIn("没有识别到文本", output.getvalue())

    async def test_http_invalid_result_is_not_reported_as_created(self):
        with tempfile.TemporaryDirectory(prefix="gcs-http-result-test-") as directory, receiving_gcs(directory) as (url, server):
            with patch.object(server.backend, "create_task", return_value={"status": "created"}):
                with self.assertRaisesRegex(ValueError, "返回结构无效"):
                    await asyncio.to_thread(GCSHTTPClient(url).create_task, VARIANTS[0])
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_cancelled_microphone_wait_stops_recorder(self):
        ready = threading.Event()
        closed = threading.Event()

        def recording(model, stop_event):
            try:
                ready.set()
                while not stop_event.wait(0.01):
                    pass
                return
                yield ""  # Keep the fixture a generator, like the actual recorder.
            finally:
                closed.set()

        with patch("inputs.speech.load_model", return_value=object()), \
             patch("inputs.speech.iter_microphone_transcripts", side_effect=recording), \
             redirect_stdout(StringIO()):
            inputs = input_texts(workflow_args("http://127.0.0.1:18001", mic=True))
            pending = asyncio.create_task(anext(inputs))
            self.assertTrue(await asyncio.to_thread(ready.wait, 2))
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            await inputs.aclose()
        self.assertTrue(closed.is_set())


if __name__ == "__main__":
    unittest.main()
