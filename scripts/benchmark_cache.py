"""Measure real-model cache use without executing the proposed ground-station tools.

Run from the project root: python -m scripts.benchmark_cache --label warm
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from assistant.tools import DEFAULT_TOOLS
from inference.prompts import SYSTEM_PROMPT
from integrations.mcp_client import MCPSession
from shared.config import AssistantConfig


def pre_launch(mode: str, **parameters) -> dict:
    return {"action": "create_task", "task": {
        "task_type": "放飞前出任务", "parameters": {"mode": mode, **parameters},
    }}


def formation(kind: str) -> dict:
    return {"action": "create_task", "task": {
        "task_type": "编队飞行任务", "parameters": {"formation_type": kind},
    }}


# 五条已知正确答案的口令，用于核对真实模型的工具调用。
CASES = {
    "新增放飞前出任务，模式为原地起飞": pre_launch("原地起飞"),
    "新增放飞前出任务，在指定位置展开，位置为东经120.1度、北纬30.2度":
        pre_launch("在指定位置展开", position={"longitude": 120.1, "latitude": 30.2}),
    "新增放飞前出任务，在指定区域展开，区域顶点为120.1,30.2；120.2,30.2；120.2,30.3":
        pre_launch("在指定区域展开", area={"vertices": [
            {"longitude": 120.1, "latitude": 30.2},
            {"longitude": 120.2, "latitude": 30.2},
            {"longitude": 120.2, "latitude": 30.3},
        ]}),
    "新增C型编队飞行任务": formation("C型"),
    "新增U型编队飞行任务": formation("U型"),
}


async def benchmark(label: str, output: Path) -> None:
    config = AssistantConfig()
    async with MCPSession(config) as gateway:
        tools = await gateway.discover()
    results = []
    async with httpx.AsyncClient(timeout=180, trust_env=False) as http:
        for text in CASES:
            payload = {
                "model": config.model,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": text}],
                "tools": tools, "tool_choice": "auto", "temperature": 0,
                "max_tokens": 2048, "stream": True,
                "stream_options": {"include_usage": True},
            }
            start = time.perf_counter()
            usage, calls = {}, {}
            async with http.stream("POST", config.url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    body = json.loads(line[6:])
                    if body.get("usage"):
                        usage = body["usage"]
                    for choice in body.get("choices", []):
                        delta = choice.get("delta") or {}
                        for call in delta.get("tool_calls") or []:
                            index = call.get("index", 0)
                            existing = calls.setdefault(index, {"function": {
                                "name": "", "arguments": "",
                            }})
                            for key in ("name", "arguments"):
                                value = (call.get("function") or {}).get(key)
                                if value:
                                    existing["function"][key] += value
            invocation = DEFAULT_TOOLS.parse({"tool_calls": list(calls.values())}, {"create_task"})
            correct = (invocation is not None
                       and invocation.arguments.payload.model_dump() == CASES[text])
            row = {"label": label, "text": text,
                   "wall_s": round(time.perf_counter() - start, 3),
                   "correct_tool_call": correct, "usage": usage}
            results.append(row)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if not correct:
                raise ValueError("模型工具调用与测试口令不一致，结果已保存")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="snapshot", help="Run label; does not change cache settings")
    parser.add_argument("--output", type=Path, default=Path("output/cache-benchmark.json"))
    args = parser.parse_args()
    asyncio.run(benchmark(args.label, args.output))


if __name__ == "__main__":
    main()
