"""对比 application/json 与 application/x-ndjson 的最小 FastAPI 示例。

运行：python -m pip install fastapi uvicorn
      uvicorn examples.fastapi_content_types:app --reload

浏览器打开 http://127.0.0.1:8000/json 或 /ndjson；也可运行：
curl.exe -N http://127.0.0.1:8000/ndjson
"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="JSON 与 NDJSON 对比")

frames = [
    {"type": "asr", "text": "新增一个任务"},
    {"type": "task_result", "text": "任务创建成功"},
]


@app.get("/json")
async def json_response() -> JSONResponse:
    """一个合法 JSON 文档；客户端通常收到完整响应后一次解析。"""
    await asyncio.sleep(2)
    return JSONResponse({"messages": frames})


async def ndjson_frames() -> AsyncIterator[bytes]:
    """逐行发送独立 JSON 对象，第二帧延迟两秒。"""
    for index, frame in enumerate(frames):
        if index:
            await asyncio.sleep(2)
        yield (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")


@app.get("/ndjson")
async def ndjson_response() -> StreamingResponse:
    """每一行都可独立解析，第一帧会先于第二帧到达。"""
    return StreamingResponse(
        ndjson_frames(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache"},
    )
