"""Manual check: send the same prompt and MCP tools to the local oMLX model."""

import asyncio
import json
import time

import requests

from inference.prompts import SYSTEM_PROMPT
from integrations.mcp_client import MCPSession
from shared.config import AssistantConfig

DEFAULT_URL = "http://127.0.0.1:18000/v1/chat/completions"
DEFAULT_MODEL = "Ornith-1.5-35B-A3B-MLX-6bit"
text = "新增C型编队飞行任务"


async def discover_tools() -> list[dict]:
    async with MCPSession(AssistantConfig()) as gateway:
        return await gateway.discover()


def main() -> None:
    tools = asyncio.run(discover_tools())
    print("发现工具：", [tool["function"]["name"] for tool in tools])

    start = time.perf_counter()
    response = requests.post(DEFAULT_URL, json={
        "model": DEFAULT_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": text}],
        "tools": tools, "tool_choice": "auto", "temperature": 0,
        "max_tokens": 2048,
    }, timeout=180)
    wall = time.perf_counter() - start
    response.raise_for_status()
    body = response.json()
    usage = body.get("usage", {})
    print(f"响应耗时：{wall:.2f} s；"
          f"输入 {usage.get('prompt_tokens')} tok（缓存命中 {usage.get('prompt_tokens_details', {}).get('cached_tokens')}）/ "
          f"输出 {usage.get('completion_tokens')} tok，"
          f"输出速率 {usage.get('completion_tokens', 0) / wall:.1f} tok/s")
    print(json.dumps(body, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
