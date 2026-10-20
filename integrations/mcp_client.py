"""MCP session adapter; discover once and reuse the child process per session."""

import os
from copy import deepcopy
import sys
from typing import Protocol

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from shared.config import AssistantConfig, PROJECT_DIR
from shared.contracts import Message
from shared.messages import AGENT, MCP, make_message


class ToolGateway(Protocol):
    async def discover(self) -> list[dict]: ...
    async def call(self, name: str, arguments: dict) -> dict: ...


def inline_refs(node, defs: dict, seen: tuple[str, ...] = ()):
    """展开 $ref/$defs：部分模型无法解析引用，会把嵌套对象序列化成字符串。"""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            return node if name in seen or name not in defs else inline_refs(
                defs[name], defs, seen + (name,))
        return {key: inline_refs(value, defs, seen)
                for key, value in node.items() if key != "$defs"}
    if isinstance(node, list):
        return [inline_refs(item, defs, seen) for item in node]
    return node


class MCPSession:
    def __init__(self, config: AssistantConfig):
        self._client = Client(StdioServerParameters(
            command=sys.executable,
            args=["-m", "integrations.mcp_server"],
            cwd=str(PROJECT_DIR),
            env={**os.environ, "TASK_OUTPUT_DIR": str(config.task_output_dir.expanduser().resolve())},
        ))
        self._tools = None

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._client.__aexit__(*exc)

    async def discover(self) -> list[dict]:
        if self._tools is None:
            discovered = (await self._client.list_tools()).tools
            self._tools = []
            for tool in discovered:
                schema = inline_refs(tool.input_schema, tool.input_schema.get("$defs", {}))
                # 传输头由 Agent 生成；模型只提出业务参数，不能编造消息编号或路由。
                schema = deepcopy(schema)
                schema.get("properties", {}).pop("header", None)
                schema["required"] = [key for key in schema.get("required", []) if key != "header"]
                if tool.name in {"upload_task", "execute_task"}:
                    schema = schema["properties"]["payload"]
                self._tools.append({"type": "function", "function": {
                    "name": tool.name, "description": tool.description or "",
                    "parameters": schema,
                }})
        return self._tools

    async def call(self, name: str, arguments: dict) -> dict:
        payload = arguments["payload"] if name == "create_task" else arguments
        request = make_message(name.upper(), payload, src=AGENT, dst=MCP)
        result = await self._client.call_tool(name, request)
        if result.is_error:
            raise ValueError("工具拒绝：" + " ".join(
                getattr(item, "text", "") for item in result.content))
        if not isinstance(result.structured_content, dict):
            raise ValueError("工具没有返回预期的结构化结果")
        message = Message[dict].model_validate(result.structured_content)
        header = message.header
        if (header.src.model_dump() != MCP or header.dst.model_dump() != AGENT):
            raise ValueError("MCP 结果的路由不一致")
        if header.msg_type == "ERROR":
            raise ValueError("工具拒绝：" + message.payload.get("error", "未知错误"))
        if header.msg_type != "TASK_RESULT":
            raise ValueError("MCP 返回了意外的消息类型")
        return message.payload
