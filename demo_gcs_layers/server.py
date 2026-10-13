"""L3: expose L4 operations through a real MCP stdio server."""

import os
import sys
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from contracts import CreateTaskPayload
from gcs_http import DEFAULT_GCS_URL, GCSHTTPClient


ground_station = GCSHTTPClient(os.environ.get("GCS_HTTP_URL", DEFAULT_GCS_URL))
mcp = MCPServer("simulated-gcs")


@mcp.tool()
def create_task(payload: CreateTaskPayload) -> dict[str, Any]:
    """新增地面站任务，仅保存示例任务，不启动飞行。
    payload.action 必须为 create_task。
    放飞前出任务：mode 为原地起飞、在指定位置展开或在指定区域展开。
    指定位置必须提供 position 的 WGS84 longitude/latitude。
    指定区域必须提供 area.vertices 的至少三个不同 WGS84 顶点。
    编队飞行任务：formation_type 必须为 C型 或 U型。
    用户未明确任务类型、模式、编队类型或必要坐标时，应先澄清。
    """
    print("[L3] 通过 HTTP POST /tasks 发送 header + payload 到模拟地面站", file=sys.stderr, flush=True)
    try:
        return ground_station.create_task(payload.model_dump())
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


if __name__ == "__main__":
    mcp.run(transport="stdio")
