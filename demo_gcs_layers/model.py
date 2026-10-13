"""L1: propose one tool call; never execute it."""

import json
import re

import httpx

from scenarios import EXAMPLE, MOCK_CASES


DEFAULT_MODEL = "Ornith-1.5-35B-A3B-MLX-6bit"
DEFAULT_URL = "http://127.0.0.1:18000/v1/chat/completions"


async def propose(text: str, tools: list[dict], *, local: bool,
                  model: str, url: str) -> dict:
    if not local:
        # A deliberately narrow fixture, not an AI or general intent recognizer.
        # ASR commonly drops punctuation; allow that without broad intent guessing.
        normalize = lambda value: re.sub(r"[\s，,。；;、！!？?（）()]", "", value).casefold()
        payload = next((payload for example, payload in MOCK_CASES.items()
                        if normalize(example) == normalize(text)), None)
        if payload is None:
            return {"content": "未新增任务：模拟模式只识别 README 中的五条完整口令。"
                    "请明确任务类型、模式或编队类型，以及展开位置/区域（如适用）。"}
        return {"tool_calls": [{"function": {
            "name": "create_task",
            "arguments": json.dumps({"payload": payload}, ensure_ascii=False),
        }}]}

    async with httpx.AsyncClient(timeout=180) as http:
        response = await http.post(url, json={
            "model": model,
            "messages": [
                {"role": "system", "content": (
                    "你是地面站新增任务指令解析器。用户明确要求新增任务且参数完整时，"
                    "只能提出一次 create_task 工具调用，参数外层必须是 payload。"
                    "payload.action 固定为 create_task，payload.task 包含 task_type 和 parameters。"
                    "task_type 只支持 放飞前出任务 或 编队飞行任务。"
                    "放飞前出任务的 parameters.mode 只支持 原地起飞、在指定位置展开、在指定区域展开。"
                    "原地起飞不带位置或区域字段；指定位置需要 position={longitude,latitude}；"
                    "指定区域需要 area={vertices:[{longitude,latitude},...]}，至少三个不同顶点。"
                    "坐标为 WGS84 十进制度，东经/北纬为正，西经/南纬为负。"
                    "编队飞行任务的 parameters 只包含 formation_type，值为 C型 或 U型。"
                    "不得混用两类任务的参数，不得编造坐标、默认选择模式或编队类型。"
                    "缺参数、有歧义、否定新增或不支持的任务时，简短澄清/说明，不调用工具。"
                    "只提出调用建议，不声称任务已经创建或飞行已经开始。"
                    "示例：新增C型编队飞行任务 → "
                    '{"payload":{"action":"create_task","task":{"task_type":"编队飞行任务",'
                    '"parameters":{"formation_type":"C型"}}}}。'
                    "用户只说新增编队飞行任务时，要询问C型还是U型。"
                )},
                {"role": "user", "content": text},
            ],
            "tools": tools, "tool_choice": "auto", "temperature": 0,
            "max_tokens": 2048,
        })
        response.raise_for_status()
        return response.json()["choices"][0]["message"]
