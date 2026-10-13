"""Show how an LLM tool call is dispatched through an MCP client."""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from mcp import Client
from mcp.client.stdio import StdioServerParameters


SERVER = Path(__file__).resolve().parent / "task_server.py"
DEFAULT_MODEL = "Ornith-1.5-35B-A3B-MLX-6bit"
DEFAULT_URL = "http://127.0.0.1:18000/v1/chat/completions"


def selected_call(response: dict, allowed_names: set[str]) -> tuple[str, dict] | None:
    """Accept at most one known, well-formed tool call from the model."""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("model response has no choices")
    message = choices[0].get("message") or {}
    calls = message.get("tool_calls") or []
    if not calls:
        return None
    if len(calls) != 1:
        raise ValueError("demo expects exactly one tool call")
    function = calls[0].get("function") or {}
    name = function.get("name")
    if name not in allowed_names:
        raise ValueError(f"model requested an unknown tool: {name!r}")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    if set(arguments) != {"title", "due_at"}:
        raise ValueError("create_task requires only title and due_at")
    if not all(isinstance(arguments[key], str) and arguments[key].strip() for key in arguments):
        raise ValueError("title and due_at must be non-empty strings")
    return name, arguments


async def run(text: str, model: str, url: str, now: datetime) -> None:
    # —— 第一步：启动 MCP 服务器子进程并连接 ——
    # StdioServerParameters 告诉客户端：用哪个解释器、跑哪个脚本、工作目录在哪。
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
        cwd=str(SERVER.parent),
    )
    print(f"转写文本：{text}", flush=True)
    # async with 确保退出时自动关闭连接、终止子进程。
    async with Client(parameters) as mcp_client:
        # —— 第二步：发现 MCP 服务器暴露了哪些工具 ——
        discovered = (await mcp_client.list_tools()).tools
        # 把 MCP 工具描述转换成 OpenAI 协议要求的 tools 格式，待会发给大模型。
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,  # JSON Schema，告诉模型参数结构
                },
            }
            for tool in discovered
        ]
        # 已注册工具名集合，用于 selected_call 做白名单校验。
        allowed_names = {tool.name for tool in discovered}
        print("MCP 发现工具：" + ", ".join(sorted(allowed_names)), flush=True)

        # —— 第三步：构造请求体，发给本地大模型 ——
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    # system prompt：约束模型只在“明确要创建提醒且有时间”时才调工具，
                    # 并要求 due_at 用带时区的 ISO 8601 格式；同时告诉模型当前时间，方便它做相对时间换算。
                    "content": (
                        "你是任务指令解析器。只有用户明确要求创建提醒或待办，且说清了具体时间时，"
                        "才调用 create_task。其他内容直接简短回答，不调用工具。"
                        "不要编造标题或时间。due_at 使用带 UTC 偏移的 ISO 8601 格式。"
                        f"当前时间：{now.isoformat(timespec='seconds')}；时区：Asia/Shanghai。"
                    ),
                },
                {"role": "user", "content": text},  # 用户输入：语音转写后的文本
            ],
            "tools": tools,            # 把 MCP 工具列表交给模型
            "tool_choice": "auto",     # 让模型自己决定要不要调工具
            "temperature": 0,          # 温度 0，输出尽量确定
            "max_tokens": 2048,
        }
        # 发送 HTTP POST 请求到本地 oMLX 服务，超时 180 秒（本地模型推理可能较慢）。
        try:
            async with httpx.AsyncClient(timeout=180.0) as http:
                reply = await http.post(url, json=payload)
                reply.raise_for_status()  # 非 2xx 状态码抛异常
                response = reply.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"oMLX 请求失败（请确认服务已启动）：{exc}") from exc

        # —— 第四步：解析模型响应，决定是否执行工具调用 ——
        call = selected_call(response, allowed_names)
        if call is None:
            # 模型没调工具，直接把它的文字回答打印出来。
            answer = response["choices"][0].get("message", {}).get("content") or "无工具调用"
            print(f"模型未调用工具：{answer}")
            return
        # 模型决定调用工具：取出工具名和参数。
        name, arguments = call
        print(f"模型提出调用：{name}({json.dumps(arguments, ensure_ascii=False)})", flush=True)

        # —— 第五步：通过 MCP 客户端真正执行工具调用 ——
        result = await mcp_client.call_tool(name, arguments)
        if result.is_error:
            # 工具执行失败（例如参数不合法），把错误详情拼出来抛异常。
            details = " ".join(getattr(item, "text", "") for item in result.content)
            raise RuntimeError(f"MCP 工具调用失败：{details}")
        # 成功：打印工具返回的结构化结果（新创建的任务对象）。
        print("MCP 返回值：" + json.dumps(result.structured_content, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Text → local LLM → MCP create_task demo")
    parser.add_argument("text", help="Speech transcript, e.g. 明天下午三点提醒我交报告")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--now", help="Override current Asia/Shanghai time for reproducible examples"
    )
    args = parser.parse_args()
    try:
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(ZoneInfo("Asia/Shanghai"))
    except ValueError:
        parser.error("--now must be a valid ISO 8601 date-time with UTC offset")
    if now.tzinfo is None or now.utcoffset() is None:
        parser.error("--now must include a UTC offset")
    try:
        asyncio.run(run(args.text, args.model, args.url, now))
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
