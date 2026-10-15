"""MCP session adapter; discover once and reuse the child process per session."""

import os
import sys
from typing import Protocol

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from shared.config import AssistantConfig, PROJECT_DIR


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
            env={**os.environ, "GCS_HTTP_URL": config.gcs_url,
                 "GCS_INTERFACE_FILE": str(config.interface_file.expanduser().resolve())},
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
                schema = tool.input_schema
                self._tools.append({"type": "function", "function": {
                    "name": tool.name, "description": tool.description or "",
                    "parameters": inline_refs(schema, schema.get("$defs", {})),
                }})
        return self._tools

    async def call(self, name: str, arguments: dict) -> dict:
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            raise ValueError("工具拒绝：" + " ".join(
                getattr(item, "text", "") for item in result.content))
        if not isinstance(result.structured_content, dict):
            raise ValueError("工具没有返回预期的结构化结果")
        return result.structured_content
