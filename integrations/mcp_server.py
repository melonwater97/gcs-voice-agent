"""L3: publish business operations as MCP tools, forwarding them through HTTP."""

import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from shared.config import DEFAULT_GCS_URL, INTERFACE_FILE
from shared.contracts import CreateTaskPayload
from .gcs_http import GCSHTTPClient


def create_server(gcs_url: str = DEFAULT_GCS_URL,
                  interface_file: Path = INTERFACE_FILE) -> MCPServer:
    ground_station = GCSHTTPClient(gcs_url, interface_file)
    server = MCPServer("simulated-gcs")

    @server.tool()
    def create_task(payload: CreateTaskPayload) -> dict[str, Any]:
        """新增地面站任务，仅保存示例任务，不启动飞行。
        payload.action 必须为 create_task。
        放飞前出任务：mode 为原地起飞、在指定位置展开或在指定区域展开。
        指定位置必须提供 position 的 WGS84 longitude/latitude。
        指定区域必须提供 area.vertices 的至少三个不同 WGS84 顶点。
        编队飞行任务：formation_type 必须为 C型 或 U型。
        用户未明确任务类型、模式、编队类型或必要坐标时，应先澄清。
        """
        print("[L3] HTTP POST /tasks：发送 header + payload 到模拟地面站",
              file=sys.stderr, flush=True)
        try:
            return ground_station.create_task(payload.model_dump())
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)) from exc

    return server


def main() -> None:
    server = create_server(os.environ.get("GCS_HTTP_URL", DEFAULT_GCS_URL),
                           Path(os.environ.get("GCS_INTERFACE_FILE", str(INTERFACE_FILE))))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
