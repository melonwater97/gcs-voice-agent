"""Task identity survives all stages; hotwords cannot silently target an unsupported model."""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from assistant.tools import verify_created_task
from assistant.workflow import Assistant, HISTORY_ROUNDS
from inputs.speech import load_hotwords, load_model, transcribe_file
from integrations.mcp_server import TaskStore
from shared.config import MODEL_DIR
from shared.contracts import CreateTaskArguments, DraftTaskResult, PreLaunchTask
from tests.test_architecture import InjectedGateway, ScriptedModel
from tests.test_workflow import VALID, message


class TaskNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_name_cannot_reach_tool_even_if_model_selects_one(self):
        first, second = "TASK-aaaaaaaa", "TASK-bbbbbbbb"
        reply = {"tool_calls": [{"function": {"name": "upload_task",
                    "arguments": json.dumps({"task_id": second})}}]}
        gateway = InjectedGateway(["create_task", "upload_task", "execute_task"],
                                  {"status": "uploaded", "task_id": second})
        assistant = Assistant(ScriptedModel([reply]), gateway)
        assistant.known_tasks = {
            first: {"task_id": first, "task_name": "晨曦", "status": "executing"},
            second: {"task_id": second, "task_name": "晨曦", "status": "draft"},
        }
        with redirect_stdout(StringIO()):
            await assistant.prepare()
            self.assertIsNone(await assistant.process_text("上传晨曦任务"))
        self.assertEqual(gateway.calls, [])
        self.assertEqual(assistant.last_events[0]["type"], "clarify")
        self.assertIn("任务编号", assistant.pending)
        self.assertEqual(assistant.history[-1][-1]["role"], "tool")
        assistant.model.replies.append(reply)
        gateway.result = {"task_id": second, "status": "uploaded", "file": "task.json",
                          "task": VALID["task"]}
        with redirect_stdout(StringIO()):
            result = await assistant.process_text(f"上传晨曦任务，编号 {second}")
        self.assertEqual(result["task_id"], second)
        self.assertEqual(len(gateway.calls), 1)

    def test_names_are_optional_but_explicit_names_must_be_valid(self):
        for value in ("", "   ", "名" * 65, 123):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                PreLaunchTask.model_validate({**VALID["task"], "task_name": value})
        self.assertEqual(PreLaunchTask.model_validate(
            {**VALID["task"], "task_name": "  晨曦  "}).task_name, "晨曦")

    def test_unnamed_tasks_receive_distinct_stable_names_and_strict_result_check(self):
        task = PreLaunchTask.model_validate({**VALID["task"], "task_name": None})
        store = TaskStore()
        first, second = store.create(task), store.create(task)
        assigned = store.entry(first)["task"]
        self.assertTrue(assigned.task_name.startswith("放飞前出任务-"))
        self.assertNotEqual(assigned.task_name, store.entry(second)["task"].task_name)
        arguments = CreateTaskArguments.model_validate({"payload": {
            "action": "create_task", "task": task.model_dump()}})
        result = DraftTaskResult(status="draft", task_id=first, task=assigned)
        verify_created_task(arguments, result)
        with self.assertRaisesRegex(ValueError, "不一致"):
            verify_created_task(arguments, result.model_copy(update={"task":
                assigned.model_copy(update={"task_name": "擅自修改"})}))
        store.mark(first, "uploaded")
        store.mark(first, "executing")
        self.assertEqual(store.entry(first)["task"].task_name, assigned.task_name)
        named = PreLaunchTask.model_validate(VALID["task"])
        self.assertEqual(store.entry(store.create(named))["task"].task_name, "晨曦")

    async def test_task_catalog_survives_history_window_and_tracks_execution(self):
        task_id = "TASK-12345678"
        gateway = InjectedGateway(["create_task", "upload_task", "execute_task"],
            {"status": "draft", "task_id": task_id, "task": VALID["task"]})
        replies = [message(VALID)] + [{"content": "需要澄清"}] * (HISTORY_ROUNDS + 1)
        replies.append({"tool_calls": [{"function": {"name": "execute_task",
            "arguments": json.dumps({"task_id": task_id})}}]})
        assistant = Assistant(ScriptedModel(replies), gateway)
        with redirect_stdout(StringIO()):
            await assistant.prepare()
            await assistant.process_text("创建晨曦任务")
            for _ in range(HISTORY_ROUNDS + 1):
                await assistant.process_text("其他问题")
            self.assertNotIn("create_task", json.dumps(assistant.history))
            state = assistant._state_messages()[0]["content"]
            self.assertIn("晨曦", state)
            self.assertIn(task_id, state)
            gateway.result = {"task_id": task_id, "status": "executing"}
            await assistant.process_text("执行晨曦")
        self.assertEqual(assistant.known_tasks[task_id]["status"], "executing")


class HotwordTests(unittest.TestCase):
    def test_domain_words_and_default_model_match_current_contract(self):
        self.assertEqual(MODEL_DIR.name, "seaco-paraformer")
        words = load_hotwords()
        self.assertTrue({"放飞前出任务", "原地起飞", "在指定位置展开", "C型", "U型",
                         "起飞高度", "打开角度", "编队长度", "编队宽度"} <= set(words))

    def test_hotwords_reach_model_and_old_checkpoint_requires_explicit_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            words = root / "hotwords.txt"
            words.write_text("\ufeff# 注释\n放飞前出\n\nC型 # 编队\n放飞前出\n", encoding="utf-8")
            self.assertEqual(load_hotwords(words), ["放飞前出", "C型"])
            config = root / "config.yaml"
            config.write_text("model: SeacoParaformer\n", encoding="utf-8")
            constructor = Mock()
            with patch.dict("sys.modules", {"funasr": SimpleNamespace(AutoModel=constructor)}):
                load_model(root, words)
                compiled = Path(constructor.call_args.kwargs["hotword"])
                self.assertEqual(compiled.read_text(), "放 飞 前 出\nC 型\n")
                constructor.reset_mock()
                config.write_text("model: Paraformer\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "不支持"):
                    load_model(root, words)
                constructor.assert_not_called()
                words.write_text("# 空词表关闭热词\n", encoding="utf-8")
                load_model(root, words)
                self.assertNotIn("hotword", constructor.call_args.kwargs)

    def test_task_names_are_added_per_recording_and_english_spaces_are_preserved(self):
        seen = []
        def generate(**kwargs):
            if "hotword" in kwargs:
                seen.append(Path(kwargs["hotword"]).read_text())
            return [{"text": "新 增 任 务 alpha beta 起 飞 高 度 五 十 米 C 型"}]
        model = SimpleNamespace(gcs_hotwords=("C型", "原地起飞"), generate=generate)
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.write_bytes(b"audio")
            text = transcribe_file(model, audio, task_names=("晨曦", "晨曦"))
            self.assertEqual(text, "新增任务alpha beta起飞高度五十米C型")
            self.assertEqual(seen, ["C 型\n原 地 起 飞\n晨 曦\n"])
            transcribe_file(model, audio)
            self.assertEqual(len(seen), 1)  # 上一条口令的任务词表不泄漏到下一条

    def test_invalid_or_missing_hotword_file_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            words = Path(directory) / "words.txt"
            with self.assertRaises(FileNotFoundError):
                load_hotwords(words)
            words.write_text("两个 词\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "空格"):
                load_hotwords(words)
