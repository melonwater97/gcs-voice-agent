"""Verify five task variants, real MCP dispatch, and rejection without writes."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from backend import SimulatedGCS
from contracts import CreateTaskPayload, INTERFACE_FILE
from demo import PROJECT_DIR, input_texts, run, validate_call
from gcs_client import GCSHTTPServer
from gcs_http import GCSHTTPClient, build_message
from model import DEFAULT_MODEL, DEFAULT_URL, propose
from scenarios import EXAMPLE, MOCK_CASES, formation, pre_launch


VALID = MOCK_CASES[EXAMPLE]
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
    {**VALID, "action": "start_flight"},
]


def message(payload: dict, name: str = "create_task") -> dict:
    return {"tool_calls": [{"function": {
        "name": name, "arguments": json.dumps({"payload": payload}, ensure_ascii=False),
    }}]}


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


def workflow_args(url: str, **overrides) -> Namespace:
    return Namespace(**{
        "text": None, "file": None, "mic": False, "local": False,
        "model": DEFAULT_MODEL, "url": DEFAULT_URL, "gcs_url": url,
        "asr_model_dir": PROJECT_DIR / "models" / "paraformer-large",
        **overrides,
    })


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_dispatch_five_variants_and_refusal(self):
        with tempfile.TemporaryDirectory(prefix="gcs-layers-test-") as directory, receiving_gcs(directory) as (url, _):
            server = Path(__file__).resolve().with_name("server.py")
            params = StdioServerParameters(command=sys.executable, args=[str(server)],
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
                for text, payload in MOCK_CASES.items():
                    with self.subTest(text=text):
                        suggestion = await propose(text, [], local=False,
                                                   model=DEFAULT_MODEL, url=DEFAULT_URL)
                        command = validate_call(suggestion, tools)
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
            validate_call(message(VALID, "start_flight"), {"create_task", "start_flight"})
        for payload in INVALID_PAYLOADS:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_call(message(payload), {"create_task"})
        self.assertIsNone(validate_call({"content": "需要澄清"}, {"create_task"}))

    def test_l2_rejects_extra_arguments_and_multiple_calls(self):
        invalid = message(VALID)
        invalid["tool_calls"][0]["function"]["arguments"] = json.dumps(
            {"payload": VALID, "header": {"msg_type": "START_FLIGHT"}})
        with self.assertRaises(ValueError):
            validate_call(invalid, {"create_task"})
        duplicate = message(VALID)
        duplicate["tool_calls"] *= 2
        with self.assertRaises(ValueError):
            validate_call(duplicate, {"create_task"})

    async def test_incomplete_negative_and_unrelated_input_has_no_call(self):
        for text in ["新增放飞前出任务", "新增编队飞行任务", "新增V型编队飞行任务",
                     "新增放飞前出任务，在指定位置展开", "不要新增C型编队飞行任务",
                     "取消新增任务", "今天天气怎么样"]:
            with self.subTest(text=text):
                reply = await propose(text, [], local=False, model=DEFAULT_MODEL, url=DEFAULT_URL)
                self.assertIsNone(validate_call(reply, {"create_task"}))

    async def test_asr_punctuation_variation_matches_fixture(self):
        reply = await propose("新增放飞前出任务模式为原地起飞。", [], local=False,
                              model=DEFAULT_MODEL, url=DEFAULT_URL)
        self.assertEqual(validate_call(reply, {"create_task"}).payload.model_dump(), VALID)

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
        self.assertEqual(CreateTaskPayload.model_validate(interface["payload"]).model_dump(), VALID)

    async def test_http_receives_full_json_and_rejects_bad_requests(self):
        with tempfile.TemporaryDirectory(prefix="gcs-http-test-") as directory, receiving_gcs(directory) as (url, _):
            async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
                self.assertEqual((await client.get("/health")).json()["status"], "ok")
                malformed = await client.post("/tasks", content="{", headers={"Content-Type": "application/json"})
                self.assertEqual(malformed.status_code, 400)
                self.assertEqual((await client.post("/tasks", json={"payload": VALID})).status_code, 422)
                invalid = build_message(VALID).model_dump()
                invalid["payload"] = formation("V型")
                self.assertEqual((await client.post("/tasks", json=invalid)).status_code, 422)
                self.assertEqual(list(Path(directory).iterdir()), [])
                request = build_message(VALID).model_dump()
                result = await client.post("/tasks", json=request)
                self.assertEqual(result.status_code, 201)
                self.assertEqual(result.json()["message"], request)

    async def test_asr_file_transcript_flows_through_mcp_and_http(self):
        with tempfile.TemporaryDirectory(prefix="gcs-asr-file-test-") as directory, receiving_gcs(directory) as (url, _):
            args = workflow_args(url, file=Path("recording.wav"))
            asr_model = object()
            with patch("asr.load_model", return_value=asr_model) as load, \
                 patch("asr.transcribe_file", return_value="新增放飞前出任务模式为原地起飞") as transcribe, \
                 redirect_stdout(StringIO()) as output:
                await run(args)
            load.assert_called_once_with(args.asr_model_dir)
            transcribe.assert_called_once_with(asr_model, args.file)
            files = list(Path(directory).glob("TASK-*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text())["payload"], VALID)
            self.assertIn("已新增放飞前出任务", output.getvalue())

    async def test_microphone_session_reuses_asr_for_multiple_commands(self):
        with tempfile.TemporaryDirectory(prefix="gcs-asr-mic-test-") as directory, receiving_gcs(directory) as (url, _):
            args = workflow_args(url, mic=True)
            with patch("asr.load_model", return_value=object()) as load, \
                 patch("asr.iter_microphone_transcripts", return_value=(text for text in [
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
                    await asyncio.to_thread(GCSHTTPClient(url).create_task, VALID)
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

        with patch("asr.load_model", return_value=object()), \
             patch("asr.iter_microphone_transcripts", side_effect=recording), \
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
