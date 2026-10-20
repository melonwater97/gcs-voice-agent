"""Verify replaceable model/tool boundaries and the consolidated CLI."""

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import BaseModel, ConfigDict

from assistant.tools import DEFAULT_TOOLS, ToolRegistry, ToolSpec
from assistant.workflow import HISTORY_ROUNDS, PENDING_MAX_CHARS, Assistant, run
from shared.config import (AssistantConfig, DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
                           DEEPSEEK_URL, PROJECT_DIR)
from inference.models import DeepSeekModel, LocalModel
from inference.prompts import SYSTEM_PROMPT
from inputs.sources import input_texts
from tests.test_workflow import (VALID, EXAMPLE, VARIANTS, pre_launch, message,
                                 openai_stub, stub_local)


class InjectedModel:
    def __init__(self, reply):
        self.reply = reply
        self.received_tools = None

    async def propose(self, messages, tools):
        self.received_tools = tools
        return self.reply


class ScriptedModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.received = []

    async def propose(self, messages, tools):
        self.received.append(messages)
        return self.replies.pop(0)


class InjectedGateway:
    def __init__(self, names, result=None):
        self.names = names
        self.result = result
        self.calls = []

    async def discover(self):
        return [{"type": "function", "function": {
            "name": name, "parameters": {}, "description": "test",
        }} for name in self.names]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


class QueryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str


class QueryResult(QueryArguments):
    status: str


def verify_query(arguments, result):
    if arguments.task_id != result.task_id:
        raise ValueError("返回任务编号不一致")


class ArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def test_added_tool_uses_own_contract_and_result_without_workflow_changes(self):
        registry = ToolRegistry([ToolSpec("query_task", QueryArguments, QueryResult,
            verify_query, lambda result: f"查询结果：{result.status}")])
        model = InjectedModel({"tool_calls": [{"function": {
            "name": "query_task", "arguments": '{"task_id":"TASK-1"}',
        }}]})
        gateway = InjectedGateway(["query_task", "unregistered"],
                                  {"task_id": "TASK-1", "status": "created"})
        assistant = Assistant(model, gateway, registry)
        with redirect_stdout(StringIO()) as output:
            await assistant.prepare()
            result = await assistant.process_text("查询任务 TASK-1")
        self.assertEqual(result["task_id"], "TASK-1")
        self.assertEqual(gateway.calls, [("query_task", {"task_id": "TASK-1"})])
        self.assertEqual([tool["function"]["name"] for tool in model.received_tools], ["query_task"])
        self.assertIn("查询结果：created", output.getvalue())

    async def test_unknown_proposal_does_not_reach_gateway(self):
        model = InjectedModel(message(VALID, "unregistered"))
        gateway = InjectedGateway(["create_task", "upload_task", "execute_task", "unregistered"])
        assistant = Assistant(model, gateway)
        with redirect_stdout(StringIO()):
            await assistant.prepare()
            with self.assertRaisesRegex(ValueError, "未知"):
                await assistant.process_text(EXAMPLE)
        self.assertEqual(gateway.calls, [])

    async def test_missing_registered_tool_fails_before_proposal(self):
        model = InjectedModel(message(VALID))
        assistant = Assistant(model, InjectedGateway([]))
        with self.assertRaisesRegex(ValueError, "缺少已注册工具"):
            await assistant.prepare()
        self.assertIsNone(model.received_tools)

    async def test_multi_turn_context_history_state_and_pending(self):
        gateway = InjectedGateway(["create_task", "upload_task", "execute_task"],
            {"status": "draft", "task_id": "TASK-1", "task": VALID["task"]})
        assistant = Assistant(ScriptedModel([
            {"content": "请问起飞高度是多少？"},          # 第一轮：澄清，缺参
            message(VALID),                               # 第二轮：用户补参后调用
        ]), gateway)
        with redirect_stdout(StringIO()):
            await assistant.prepare()
            self.assertIsNone(await assistant.process_text("新增原地起飞任务"))
            self.assertEqual(assistant.pending, "请问起飞高度是多少？")
            result = await assistant.process_text("起飞高度50米")
        self.assertEqual(result["task_id"], "TASK-1")
        self.assertIsNone(assistant.pending)
        self.assertIn("TASK-1", assistant.last_task)
        self.assertIn("draft", assistant.last_task)
        self.assertIn("原地起飞", assistant.last_task)
        second = assistant.model.received[1]
        self.assertEqual(len(second), 6)
        self.assertEqual(second[0]["content"], SYSTEM_PROMPT)
        self.assertIn("待补全", second[1]["content"])
        self.assertEqual(second[2], {"role": "user", "content": "新增原地起飞任务"})
        self.assertEqual(second[3], {"role": "assistant", "content": "请问起飞高度是多少？"})
        self.assertEqual(second[4]["role"], "system")
        self.assertIn("待补全", second[4]["content"])
        self.assertEqual(second[5], {"role": "user", "content": "起飞高度50米"})
        self.assertEqual(assistant.history[-1][-1]["role"], "tool")

    async def test_long_clarify_is_delivered_whole_while_pending_stays_compact(self):
        answer = "请补充起飞高度、编队类型和展开位置。" * 20     # 远超 PENDING_MAX_CHARS
        assistant = Assistant(InjectedModel({"content": answer}),
                              InjectedGateway(["create_task", "upload_task", "execute_task"]))
        with redirect_stdout(StringIO()):
            await assistant.prepare()
            self.assertIsNone(await assistant.process_text("新增编队任务"))
        events = [event for event in assistant.last_events if event["type"] == "clarify"]
        self.assertEqual(events[0]["text"], answer)               # 气泡展示完整答复
        self.assertEqual(assistant.pending, answer[:PENDING_MAX_CHARS])  # 待补全目标保持精简

    async def test_history_window_keeps_recent_rounds_only(self):
        assistant = Assistant(ScriptedModel([{"content": f"澄清{i}"} for i in range(6)]),
                              InjectedGateway(["create_task", "upload_task", "execute_task"]))
        with redirect_stdout(StringIO()):
            await assistant.prepare()
            for index in range(6):
                self.assertIsNone(await assistant.process_text(f"第{index}句"))
        sent = assistant.model.received[-1]
        self.assertNotIn({"role": "user", "content": "第0句"}, sent)   # 最早的轮次被淘汰
        self.assertIn({"role": "user", "content": "第1句"}, sent)
        self.assertIn({"role": "user", "content": "第4句"}, sent)      # 最近完整轮保留
        self.assertEqual(sent[-1], {"role": "user", "content": "第5句"})
        # system + 状态块 + 4 轮历史 + 补参提示 + 当前输入
        self.assertEqual(len(sent), 1 + 1 + HISTORY_ROUNDS * 2 + 1 + 1)

    async def test_mismatched_result_is_not_reported_as_success(self):
        mismatched = pre_launch("原地起飞", takeoff_altitude=99)
        gateway = InjectedGateway(["create_task", "upload_task", "execute_task"],
                                  {"status": "draft", "task_id": "TASK-1", "task": mismatched["task"]})
        assistant = Assistant(InjectedModel(message(VALID)), gateway)
        with redirect_stdout(StringIO()) as output:
            await assistant.prepare()
            with self.assertRaisesRegex(ValueError, "不一致"):
                await assistant.process_text(EXAMPLE)
        self.assertNotIn("已创建", output.getvalue())

    async def test_local_model_sends_discovered_schema_and_returns_message(self):
        tools = [{"type": "function", "function": {"name": "create_task", "parameters": {}}}]
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": EXAMPLE}]
        def reply(request):
            body = json.loads(request.content)
            self.assertEqual(body["tools"], tools)
            self.assertEqual(body["messages"], messages)
            self.assertEqual(body["tool_choice"], "auto")
            return httpx.Response(200, json={"choices": [{"message": message(VALID)}]})
        model = LocalModel(transport=httpx.MockTransport(reply))
        self.assertEqual(await model.propose(messages, tools), message(VALID))

    async def test_local_model_rejects_malformed_response_and_http_failure(self):
        for body in [[], {}, {"choices": []}, {"choices": [None]},
                     {"choices": [{"message": "bad"}]}]:
            with self.subTest(body=body):
                model = LocalModel(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=body)))
                with self.assertRaisesRegex(ValueError, "返回结构无效"):
                    await model.propose([{"role": "user", "content": EXAMPLE}], [])
        model = LocalModel(transport=httpx.MockTransport(
            lambda request: httpx.Response(503)))
        with self.assertRaisesRegex(ValueError, "oMLX 请求失败"):
            await model.propose([{"role": "user", "content": EXAMPLE}], [])

    async def test_deepseek_model_sends_auth_header_and_returns_message(self):
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": EXAMPLE}]
        def reply(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], DEEPSEEK_MODEL)
            self.assertEqual(body["messages"], messages)
            self.assertEqual(body["tool_choice"], "auto")
            return httpx.Response(200, json={"choices": [{"message": message(VALID)}]})
        model = DeepSeekModel(api_key="test-key", transport=httpx.MockTransport(reply))
        self.assertEqual(await model.propose(messages, []), message(VALID))
        cloud = DeepSeekModel(api_key="test-key", transport=httpx.MockTransport(
            lambda request: httpx.Response(401)))
        with self.assertRaisesRegex(ValueError, "DeepSeek 请求失败"):
            await cloud.propose(messages, [])

    async def test_deepseek_config_switches_defaults_and_requires_key(self):
        config = AssistantConfig(deepseek=True)
        self.assertEqual(config.model, DEEPSEEK_MODEL)
        self.assertEqual(config.url, DEEPSEEK_URL)
        self.assertEqual(config.api_key, DEEPSEEK_API_KEY)
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
            await run(AssistantConfig(deepseek=True, api_key=""))

    async def test_text_input_does_not_load_speech(self):
        with patch("inputs.speech.load_model", side_effect=AssertionError("ASR loaded")):
            self.assertEqual([text async for text in input_texts(AssistantConfig(text=EXAMPLE))], [EXAMPLE])
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c",
            "import sys; from assistant.workflow import run; "
            "assert not any(name in sys.modules for name in "
            "['funasr', 'torch', 'sounddevice', 'inputs.speech'])"],
            cwd=PROJECT_DIR, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_custom_task_output_directory_is_forwarded_to_mcp(self):
        class CreateThenUpload:
            """第一轮创建任务，第二轮从历史工具结果中提取任务编号并上传。"""

            def __init__(self):
                self.rounds = 0

            async def propose(self, messages, tools):
                self.rounds += 1
                if self.rounds == 1:
                    return message(VALID)
                tool = next(m for m in messages if m.get("role") == "tool")
                task_id = json.loads(tool["content"])["task_id"]
                return {"tool_calls": [{"function": {
                    "name": "upload_task",
                    "arguments": json.dumps({"task_id": task_id})}}]}

        async def two_texts(config):
            yield "新增原地起飞任务，起飞高度50米"
            yield "上传任务"

        with tempfile.TemporaryDirectory() as directory:
            with patch("assistant.workflow.LocalModel", lambda *a, **k: CreateThenUpload()), \
                 patch("assistant.workflow.input_texts", two_texts), \
                 redirect_stdout(StringIO()) as output:
                await run(AssistantConfig(task_output_dir=Path(directory)))
            self.assertIn("已保存并模拟上传放飞前出任务", output.getvalue())
            files = list(Path(directory).glob("TASK-*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text())["task"], VALID["task"])

    async def test_cancel_during_inference_closes_suspended_input(self):
        closed = asyncio.Event()

        async def inputs(config):
            try:
                yield EXAMPLE
            finally:
                closed.set()

        class Session:
            async def __aenter__(self):
                return InjectedGateway(["create_task", "upload_task", "execute_task"])

            async def __aexit__(self, *exc):
                return False

        with patch("assistant.workflow.MCPSession", return_value=Session()), \
             patch("assistant.workflow.input_texts", side_effect=inputs), \
             patch.object(Assistant, "process_text", side_effect=asyncio.CancelledError), \
             redirect_stdout(StringIO()):
            with self.assertRaises(asyncio.CancelledError):
                await run(AssistantConfig(mic=True))
        self.assertTrue(closed.is_set())

    def test_config_and_registry_reject_invalid_setup(self):
        with self.assertRaises(ValueError):
            AssistantConfig(text=EXAMPLE, mic=True)
        spec = ToolSpec("query_task", QueryArguments, QueryResult, verify_query, str)
        with self.assertRaises(ValueError):
            ToolRegistry([spec, spec])
        for calls in ["bad", [None], [{}], [{"function": {"name": []}}]]:
            with self.subTest(calls=calls), self.assertRaises(ValueError):
                DEFAULT_TOOLS.parse({"tool_calls": calls}, {"create_task"})


class CLITests(unittest.TestCase):
    def test_cli_entrypoint_dispatch(self):
        with tempfile.TemporaryDirectory() as directory, \
                openai_stub(message(VALID)) as model_url:
            result = subprocess.run([sys.executable, str(PROJECT_DIR / "main.py"), "assistant",
                "--task-output-dir", directory, "--url", model_url, EXAMPLE], cwd=directory,
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("已创建放飞前出任务", result.stdout)
            # 阶段一仅创建草稿，地面站不产生任务文件
            self.assertEqual(list(Path(directory).glob("TASK-*.json")), [])

    def test_cli_rejects_conflicting_input_before_starting_mcp(self):
        result = subprocess.run([sys.executable, str(PROJECT_DIR / "main.py"), "assistant",
            EXAMPLE, "--mic"], cwd=PROJECT_DIR, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("只能选择一个", result.stderr)
        self.assertNotIn("发现 MCP 工具", result.stdout)
