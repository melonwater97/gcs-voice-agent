"""MCP 工具业务请求/响应统一为 header + payload，stdio 协议由 MCP SDK 承载。"""

import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from shared.config import OUTPUT_DIR
from shared.contracts import CreateTaskPayload, DraftTaskResult, MessageHeader, TaskIdPayload
from shared.messages import MCP, make_message, check_route
from simulator.backend import TaskStore


def create_server(task_output_dir: Path = OUTPUT_DIR) -> MCPServer:
    tasks = TaskStore(task_output_dir)
    server = MCPServer("task-simulator")

    def invoke(header, action, operation):
        try:
            check_route(header, action.upper(), "BACKGROUND", MCP)
            return make_message("TASK_RESULT", operation(), src=MCP, request=header)
        except (ValueError, OSError) as exc:
            return make_message("ERROR", {"error": str(exc)}, src=MCP, request=header)

    @server.tool()
    def create_task(header: MessageHeader, payload: CreateTaskPayload) -> dict[str, Any]:
        """阶段一：创建任务并设置参数，分配任务编号，保存为草稿（draft），不上传、不执行。
        放飞前出任务 parameters.mode 为 原地起飞 或 在指定位置展开：
        - 原地起飞：需要 takeoff_altitude（起飞高度，米）。
        - 在指定位置展开：需要 takeoff_altitude 和 formation；
          C型需要 open_angle（角度）、radius（半径，米）、position（WGS84 longitude/latitude）；
          U型需要 formation_length（长度，米）、formation_width（宽度，米）、position。
        缺少模式、高度或编队参数时先澄清，不调用本工具。
        task.task_name 保存用户名称，未指定时省略，由服务生成；名称不代替 task_id。
        """
        def create():
            task_id = tasks.create(payload.task)
            return DraftTaskResult(status="draft", task_id=task_id,
                                   task=tasks.entry(task_id)["task"]).model_dump()
        print("[MCP] 创建任务草稿", file=sys.stderr, flush=True)
        return invoke(header, "create_task", create)

    @server.tool()
    def upload_task(header: MessageHeader, payload: TaskIdPayload) -> dict[str, Any]:
        """阶段二：保存并模拟上传草稿，状态变为 uploaded，仍未开始飞行。
        task_id 必须是本会话 create_task 返回的编号；未创建或已上传时拒绝。
        """
        print("[MCP] 保存文件并模拟上传", file=sys.stderr, flush=True)
        return invoke(header, "upload_task", lambda: tasks.upload(payload.task_id))

    @server.tool()
    def execute_task(header: MessageHeader, payload: TaskIdPayload) -> dict[str, Any]:
        """阶段三：模拟执行已上传任务，状态变为 executing；task_id 使用已上传任务编号。
        尚未创建、未上传或已执行时拒绝，结果由 Agent 返回界面。
        """
        print("[MCP] 模拟执行任务", file=sys.stderr, flush=True)
        return invoke(header, "execute_task", lambda: tasks.execute(payload.task_id))

    return server


def main() -> None:
    create_server(Path(os.environ.get("TASK_OUTPUT_DIR", str(OUTPUT_DIR)))).run(transport="stdio")


if __name__ == "__main__":
    main()
