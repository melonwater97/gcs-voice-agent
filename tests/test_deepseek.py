"""Manual check: send the same prompt and MCP tools to the DeepSeek API."""

import asyncio
import json
import time

import requests

from inference.prompts import SYSTEM_PROMPT
from integrations.mcp_client import MCPSession
from shared.config import AssistantConfig, DEEPSEEK_API_KEY

API_KEY = DEEPSEEK_API_KEY  # 统一在 shared/config.py 配置，可用环境变量 DEEPSEEK_API_KEY 覆盖
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"  # 支持 function calling；deepseek-reasoner 不支持
text = "新增C型编队飞行任务"


async def discover_tools() -> list[dict]:
    async with MCPSession(AssistantConfig()) as gateway:
        return await gateway.discover()


def main() -> None:
    tools = asyncio.run(discover_tools())
    print("发现工具：", [tool["function"]["name"] for tool in tools])
    start = time.perf_counter()
    response = requests.post(API_URL, json={
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": text}],
        "tools": tools, "tool_choice": "auto", "temperature": 0,
        "max_tokens": 2048,
    }, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=60)
    wall = time.perf_counter() - start
    response.raise_for_status()
    body = response.json()
    usage = body.get("usage", {})
    print(f"响应耗时：{wall:.2f} s；"
          f"输入 {usage.get('prompt_tokens')} tok / "
          f"输出 {usage.get('completion_tokens')} tok，"
          f"输出速率 {usage.get('completion_tokens', 0) / wall:.1f} tok/s")
    message = body["choices"][0]["message"]
    print(json.dumps(message, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
