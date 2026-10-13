"""L2: explicit workflow, plus the MCP client side of L3."""

import argparse
import asyncio
import json
import os
import sys
import threading
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from contracts import CreateTaskArguments, CreateTaskResult, describe_task
from gcs_http import DEFAULT_GCS_URL
from model import DEFAULT_MODEL, DEFAULT_URL, EXAMPLE, propose


PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.append(str(PROJECT_DIR))


def log(layer: str, message: str) -> None:
    print(f"[{layer}] {message}", flush=True)


def error_details(exc: BaseException) -> str:
    # MCP's async context may wrap a workflow error in nested task groups.
    if isinstance(exc, BaseExceptionGroup):
        return "\n".join(error_details(item) for item in exc.exceptions)
    return str(exc)


def validate_call(message: dict, allowed: set[str]) -> CreateTaskArguments | None:
    calls = message.get("tool_calls") or []
    if not calls:
        return None
    if len(calls) != 1:
        raise ValueError("此教学流程每次只接受一个新增任务调用")
    function = calls[0]["function"]
    if function["name"] not in allowed or function["name"] != "create_task":
        raise ValueError("拒绝未知或不属于当前流程的工具")
    arguments = function["arguments"]
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    return CreateTaskArguments.model_validate(arguments)


async def call(client: Client, name: str, arguments: dict) -> dict:
    log("L3", f"MCP call_tool: {name} {json.dumps(arguments, ensure_ascii=False)}")
    result = await client.call_tool(name, arguments)
    if result.is_error:
        raise ValueError("工具拒绝：" + " ".join(
            getattr(item, "text", "") for item in result.content))
    if not isinstance(result.structured_content, dict):
        raise ValueError("工具没有返回预期的结构化结果")
    return result.structured_content


def next_transcript(recordings):
    # StopIteration must not propagate through asyncio's thread future.
    return next(recordings, None)


async def input_texts(args: argparse.Namespace):
    if args.file is None and not args.mic:
        yield args.text if args.text is not None else EXAMPLE
        return
    import asr

    log("ASR", "加载本地 Paraformer；本次会话只加载一次")
    asr_model = await asyncio.to_thread(asr.load_model, args.asr_model_dir)
    if args.file is not None:
        text = await asyncio.to_thread(asr.transcribe_file, asr_model, args.file)
        log("ASR", "转写：" + text)
        yield text
        return

    stop = threading.Event()
    recordings = asr.iter_microphone_transcripts(asr_model, stop_event=stop)
    try:
        while True:
            pending = asyncio.create_task(asyncio.to_thread(next_transcript, recordings))
            try:
                text = await asyncio.shield(pending)
            except asyncio.CancelledError:
                stop.set()
                await pending  # Wait for the recorder to restore the terminal and close audio.
                raise
            if text is None:
                break
            log("ASR", "转写：" + text)
            yield text
    finally:
        stop.set()
        recordings.close()


async def process_text(client: Client, args: argparse.Namespace, text: str,
                       tools: list[dict], allowed: set[str]) -> dict | None:
    text = text.strip()
    if not text:
        log("ASR", "没有识别到文本，请重试")
        return None
    log("输入", text)
    message = await propose(text, tools, local=args.local, model=args.model, url=args.url)
    log("L1", "提出动作：" + json.dumps(message, ensure_ascii=False))
    command = validate_call(message, allowed)
    if command is None:
        log("L2", "无工具调用，结束：" + (message.get("content") or "需要澄清"))
        return None

    log("L2", "工具白名单、任务类型及对应参数校验通过")
    log("L2", "接口 payload：" + json.dumps(command.payload.model_dump(), ensure_ascii=False))
    result = await call(client, "create_task", command.model_dump())
    log("L3", "MCP 返回：" + json.dumps(result, ensure_ascii=False))
    validated = CreateTaskResult.model_validate(result)
    if validated.message.payload != command.payload:
        raise ValueError("地面站返回的任务与调用建议不一致")
    log("结果", f"已新增{describe_task(validated.message.payload)}；"
        f"任务编号：{validated.task_id}。模拟地面站已保存，未启动飞行。")
    return result


async def run(args: argparse.Namespace) -> None:
    log("L1", "本地 oMLX 模型" if args.local else "模拟模型输出（五条固定口令，不进行 AI 推理）")
    log("地面站", args.gcs_url)
    server = Path(__file__).resolve().with_name("server.py")
    async with Client(StdioServerParameters(
        command=sys.executable, args=[str(server)], cwd=str(server.parent),
        env={**os.environ, "GCS_HTTP_URL": args.gcs_url},
    )) as client:
        discovered = (await client.list_tools()).tools
        allowed = {tool.name for tool in discovered}
        log("L3", "发现 MCP 工具：" + ", ".join(sorted(allowed)))
        tools = [{"type": "function", "function": {
            "name": tool.name, "description": tool.description or "",
            "parameters": tool.input_schema,
        }} for tool in discovered if tool.name == "create_task"]

        async for text in input_texts(args):
            try:
                await process_text(client, args, text, tools, allowed)
            except Exception as exc:
                if not args.mic:
                    raise
                log("错误", error_details(exc))


def main() -> None:
    parser = argparse.ArgumentParser(description="Text/audio → ASR → LLM → MCP → HTTP GCS")
    parser.add_argument("text", nargs="?", help="Text input; omit for the default example")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="Transcribe a local audio recording")
    source.add_argument("--mic", action="store_true", help="Microphone input with e/e/q")
    parser.add_argument("--asr-model-dir", type=Path, default=PROJECT_DIR / "models" / "paraformer-large")
    parser.add_argument("--local", action="store_true", help="Use local oMLX instead of fixture")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--gcs-url", default=DEFAULT_GCS_URL, help="GCS HTTP service base URL")
    args = parser.parse_args()
    if args.text is not None and (args.file is not None or args.mic):
        parser.error("text、--file 和 --mic 只能选择一个")
    if args.mic and not sys.stdin.isatty():
        parser.error("--mic requires an interactive terminal")
    if args.file is not None and not args.file.expanduser().resolve().is_file():
        parser.error(f"Audio file does not exist: {args.file}")
    try:
        asyncio.run(run(args))
    except Exception as exc:
        parser.exit(1, f"[错误] {error_details(exc)}\n")
    except KeyboardInterrupt:
        print("\n已退出。", flush=True)


if __name__ == "__main__":
    main()
