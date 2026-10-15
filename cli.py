"""Unified entry points for the assistant, HTTP simulator and MCP server."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from shared.config import (AssistantConfig, DEFAULT_GCS_URL, DEFAULT_MODEL, DEFAULT_URL,
                     INTERFACE_FILE, MODEL_DIR, OUTPUT_DIR)
from shared.reporting import error_details


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="地面站助手：文本/语音 → LLM → MCP → HTTP")
    commands = parser.add_subparsers(dest="command", required=True)
    assistant = commands.add_parser("assistant", help="启动文本或语音助手")
    assistant.add_argument("text", nargs="?", help="省略时使用默认示例")
    source = assistant.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="本地录音文件")
    source.add_argument("--mic", action="store_true", help="e/e/q 连续麦克风录音")
    assistant.add_argument("--asr-model-dir", type=Path, default=MODEL_DIR)
    assistant.add_argument("--deepseek", action="store_true",
                           help="使用 DeepSeek 云端模型；需设置环境变量 DEEPSEEK_API_KEY")
    assistant.add_argument("--model", default=DEFAULT_MODEL)
    assistant.add_argument("--url", default=DEFAULT_URL)
    assistant.add_argument("--gcs-url", default=DEFAULT_GCS_URL)
    assistant.add_argument("--interface-file", type=Path, default=INTERFACE_FILE)
    gcs = commands.add_parser("gcs", help="启动地面站 HTTP 模拟服务")
    gcs.add_argument("--host", default="127.0.0.1")
    gcs.add_argument("--port", type=int, default=18001)
    gcs.add_argument("--output-dir", type=Path,
                     default=Path(os.environ.get("GCS_LAYERS_OUTPUT", OUTPUT_DIR)))
    mcp = commands.add_parser("mcp", help="启动 MCP stdio 服务（通常由助手自动启动）")
    mcp.add_argument("--gcs-url", default=os.environ.get("GCS_HTTP_URL", DEFAULT_GCS_URL))
    mcp.add_argument("--interface-file", type=Path,
                     default=Path(os.environ.get("GCS_INTERFACE_FILE", INTERFACE_FILE)))
    return parser


def serve_gcs(args) -> None:
    from simulator.http_server import GCSHTTPServer

    server = GCSHTTPServer((args.host, args.port), args.output_dir.expanduser().resolve())
    print(f"地面站模拟程序：http://{args.host}:{server.server_port}；POST /tasks，GET /health", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


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
                parser.error("--deepseek 需要配置 API Key（shared/config.py 或环境变量 DEEPSEEK_API_KEY）")
            if config.file is not None and not config.file.expanduser().resolve().is_file():
                parser.error(f"Audio file does not exist: {config.file}")
            if not config.interface_file.expanduser().resolve().is_file():
                parser.error(f"Interface template does not exist: {config.interface_file}")
            from assistant.workflow import run
            asyncio.run(run(config))
        elif args.command == "gcs":
            serve_gcs(args)
        elif args.command == "mcp":
            from integrations.mcp_server import create_server
            create_server(args.gcs_url, args.interface_file.expanduser().resolve()).run(transport="stdio")
    except KeyboardInterrupt:
        print("\n已退出。", flush=True)
    except Exception as exc:
        parser.exit(1, f"[错误] {error_details(exc)}\n")
