"""Command-line entry points for Agent, text/voice assistant and MCP tools."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from shared.config import (AssistantConfig, DEFAULT_MODEL, DEFAULT_URL,
                           ASR_HOTWORDS_FILE, MODEL_DIR, OUTPUT_DIR)
from shared.reporting import error_details


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="React 地面站 → Agent → MCP 任务模拟")
    commands = parser.add_subparsers(dest="command", required=True)
    assistant = commands.add_parser("assistant", help="启动文本或语音助手")
    assistant.add_argument("text", nargs="?", help="省略时使用默认示例")
    source = assistant.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="本地录音文件")
    source.add_argument("--mic", action="store_true", help="e/e/q 连续麦克风录音")
    web = commands.add_parser("web", help="启动 React 地面站直接调用的 Agent 服务")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=18002)
    web.add_argument("--record-max-seconds", type=int, default=90,
                     help="服务端录音单次最长时长，单位秒")
    for command in (assistant, web):
        command.add_argument("--asr-model-dir", type=Path, default=MODEL_DIR)
        command.add_argument("--asr-hotwords-file", type=Path, default=ASR_HOTWORDS_FILE,
                             help="ASR 热词表，每行一个词；指定空文件可关闭热词")
        command.add_argument("--deepseek", action="store_true",
                             help="使用 DeepSeek 云端模型；需设置环境变量 DEEPSEEK_API_KEY")
        command.add_argument("--model", default=DEFAULT_MODEL)
        command.add_argument("--url", default=DEFAULT_URL)
        command.add_argument("--task-output-dir", type=Path, default=OUTPUT_DIR,
                             help="Agent 侧模拟上传任务的文件目录")
    mcp = commands.add_parser("mcp", help="启动 MCP stdio 服务（通常由 Agent 自动启动）")
    mcp.add_argument("--task-output-dir", type=Path,
                     default=Path(os.environ.get("TASK_OUTPUT_DIR", OUTPUT_DIR)))
    return parser


def serve_web(args) -> None:
    from agent.http_server import build_runtime

    values = vars(args).copy()
    for key in ("command", "host", "port"):
        values.pop(key)
    config = AssistantConfig(**values)
    runtime = build_runtime(config, args.host, args.port)
    print(f"Agent 服务：http://{args.host}:{runtime.server.server_port}；"
          "POST /api/voice，POST /api/text，POST /api/state，POST /health", flush=True)
    try:
        runtime.server.serve_forever()
    finally:
        runtime.close()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "assistant":
            values = vars(args).copy()
            values.pop("command")
            config = AssistantConfig(**values)
            if config.mic and not sys.stdin.isatty():
                parser.error("--mic requires an interactive terminal")
            if config.deepseek and not config.api_key:
                parser.error("--deepseek 需要配置环境变量 DEEPSEEK_API_KEY")
            if config.file is not None and not config.file.expanduser().resolve().is_file():
                parser.error(f"Audio file does not exist: {config.file}")
            from assistant.workflow import run
            asyncio.run(run(config))
        elif args.command == "web":
            if args.deepseek and not os.environ.get("DEEPSEEK_API_KEY", ""):
                parser.error("--deepseek 需要配置环境变量 DEEPSEEK_API_KEY")
            serve_web(args)
        elif args.command == "mcp":
            from integrations.mcp_server import create_server
            create_server(args.task_output_dir).run(transport="stdio")
    except KeyboardInterrupt:
        print("\n已退出。", flush=True)
    except Exception as exc:
        parser.exit(1, f"[错误] {error_details(exc)}\n")
