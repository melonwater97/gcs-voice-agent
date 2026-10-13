"""MCP 协议与工具分发的集中校验；不依赖任何大模型，纯协议层测试。

本测试覆盖三件事：
1) MCP 客户端能否发现 create_task 工具、能否正常调用并落盘；
2) 非法参数 / 不存在的工具是否被正确拒绝；
3) client.selected_call 对工具响应的解析与未知工具的报错。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# mcp.Client：MCP 官方 SDK 提供的客户端，负责用 JSON-RPC 跟 stdio 服务器通信。
# StdioServerParameters：描述“如何启动一个 MCP 服务器进程”的参数。
from mcp import Client
from mcp.client.stdio import StdioServerParameters

# selected_call：从 client.py 导入的辅助函数，
# 作用是从大模型返回的 tool_calls 里挑出第一个“已注册工具”的调用。
from client import selected_call


# 被测的 MCP 服务器脚本路径：本文件所在目录下的 task_server.py。
SERVER = Path(__file__).resolve().parent / "task_server.py"


# IsolatedAsyncioTestCase：unittest 提供的异步测试基类，
# 每个测试方法在独立的 asyncio 事件循环里执行，避免测试之间互相污染。
class TaskDemoTests(unittest.IsolatedAsyncioTestCase):
    # 测试 1：MCP 工具发现 + 正常创建 + 非法拒绝（端到端，真的起一个子进程服务器）。
    async def test_mcp_discovery_creation_and_rejections(self) -> None:
        # Given：在临时目录里放一个干净的 tasks.json，
        #        并通过 DEMO_TASKS_FILE 环境变量告诉服务器任务文件放这里（不污染真实文件）。
        with tempfile.TemporaryDirectory() as directory:
            tasks_file = Path(directory) / "tasks.json"
            # 构造启动参数：用当前 Python 解释器运行 task_server.py，
            # 工作目录设为 task_server.py 所在目录，确保相对路径与 import 都正确。
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(SERVER)],
                env={**os.environ, "DEMO_TASKS_FILE": str(tasks_file)},
                cwd=str(SERVER.parent),
            )
            # When：用 async with 启动一个 MCP 客户端并连接到服务器进程。
            #       退出 with 块时会自动关闭连接、杀掉子进程。
            async with Client(parameters) as mcp_client:
                # 断言 1：list_tools 返回的工具名集合只有 create_task。
                names = {tool.name for tool in (await mcp_client.list_tools()).tools}
                self.assertEqual(names, {"create_task"})

                # 断言 2：合法参数调用 create_task，is_error 为 False，且 tasks.json 里多了一条记录。
                good = await mcp_client.call_tool(
                    "create_task", {"title": "交报告", "due_at": "2026-10-14T15:00:00+08:00"}
                )
                self.assertFalse(good.is_error)
                tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["title"], "交报告")

                # 断言 3：非法时间格式调用，服务器应返回 is_error=True（不抛异常、不写坏文件）。
                invalid = await mcp_client.call_tool(
                    "create_task", {"title": "错误时间", "due_at": "明天下午三点"}
                )
                self.assertTrue(invalid.is_error)

                # 断言 4：调用不存在的工具，同样应被拒绝。
                unknown = await mcp_client.call_tool("delete_all_tasks", {})
                self.assertTrue(unknown.is_error)

                # 断言 5：两次失败调用都没有写入任务，文件里仍然只有 1 条记录。
                self.assertEqual(len(json.loads(tasks_file.read_text(encoding="utf-8"))), 1)

    # 测试 2：大模型返回了一个“未注册”的工具调用时，selected_call 应抛 ValueError。
    #         这样 Agent 不会把请求转发给 MCP 服务器（服务器端也会拒绝，但提前拦截更清晰）。
    def test_unknown_model_tool_is_not_dispatched(self) -> None:
        # 模拟大模型的响应结构：choices[0].message.tool_calls[0].function
        response = {
            "choices": [{"message": {"tool_calls": [{"function": {
                "name": "delete_all_tasks", "arguments": "{}"
            }}]}}]
        }
        # 传入已注册工具集合 {"create_task"}，期望抛出 ValueError 且消息含 "unknown tool"。
        with self.assertRaisesRegex(ValueError, "unknown tool"):
            selected_call(response, {"create_task"})

    # 测试 3：大模型没有发起任何工具调用时，selected_call 应返回 None。
    def test_no_tool_call(self) -> None:
        self.assertIsNone(selected_call({"choices": [{"message": {"content": "你好"}}]}, {"create_task"}))


if __name__ == "__main__":
    unittest.main()
