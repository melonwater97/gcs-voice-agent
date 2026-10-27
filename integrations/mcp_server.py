"""Python MCP 服务：支持旧版消息信封和 Pi 直接使用的业务参数。"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from shared.config import OUTPUT_DIR
from shared.contracts import CreateTaskPayload, DraftTaskResult, MessageHeader, TaskIdPayload, Task, TaskId
from shared.messages import AGENT, MCP, make_message, check_route
from simulator.backend import TaskStore
from integrations.groundstation_http import GroundStationHTTP, load_config


def create_server(task_output_dir: Path = OUTPUT_DIR, task_state_path: Path | None = None,
                  *, agent_tools: bool = False, config_path: Path | None = None,
                  request_dir: Path | None = None) -> MCPServer:
    http = GroundStationHTTP(load_config(config_path), request_dir or config_path.parent / "requests") if config_path else None
    tasks = None if http else TaskStore(task_output_dir, state_path=task_state_path)
    server = MCPServer("groundstation-tools" if http else "task-simulator")

    def invoke(header, action, operation):
        try:
            check_route(header, action.upper(), "BACKGROUND", MCP)
            return make_message("TASK_RESULT", operation(), src=MCP, request=header)
        except (ValueError, OSError) as exc:
            return make_message("ERROR", {"error": str(exc)}, src=MCP, request=header)

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

    def upload_task(header: MessageHeader, payload: TaskIdPayload) -> dict[str, Any]:
        """阶段二：保存并模拟上传草稿，状态变为 uploaded，仍未开始飞行。
        task_id 必须是本会话 create_task 返回的编号；未创建或已上传时拒绝。
        """
        print("[MCP] 保存文件并模拟上传", file=sys.stderr, flush=True)
        return invoke(header, "upload_task", lambda: tasks.upload(payload.task_id))

    def execute_task(header: MessageHeader, payload: TaskIdPayload) -> dict[str, Any]:
        """阶段三：模拟执行已上传任务，状态变为 executing；task_id 使用已上传任务编号。
        尚未创建、未上传或已执行时拒绝，结果由 Agent 返回界面。
        """
        print("[MCP] 模拟执行任务", file=sys.stderr, flush=True)
        return invoke(header, "execute_task", lambda: tasks.execute(payload.task_id))

    if agent_tools:
        # Pi sees business arguments only. Legacy envelopes remain internal to Python.
        def header(action: str) -> MessageHeader:
            return MessageHeader.model_validate(make_message(action.upper(), {}, src=AGENT, dst=MCP)["header"])

        def result(message: dict) -> dict[str, Any]:
            if message["header"]["msg_type"] == "ERROR":
                raise ValueError(message["payload"]["error"])
            return {**message["payload"], "simulation": True, "source": "python-task-simulator"}

        def create_agent_task(task: Task) -> dict[str, Any]:
            if http:
                return http.call("create_task", {"task": task.model_dump()})
            return result(create_task(header("create_task"), CreateTaskPayload(action="create_task", task=task)))

        def upload_agent_task(task_id: TaskId) -> dict[str, Any]:
            if http:
                return http.call("upload_task", {"task_id": task_id})
            return result(upload_task(header("upload_task"), TaskIdPayload(task_id=task_id)))

        def execute_agent_task(task_id: TaskId) -> dict[str, Any]:
            if http:
                return http.call("execute_task", {"task_id": task_id})
            return result(execute_task(header("execute_task"), TaskIdPayload(task_id=task_id)))

        for original, direct in [(create_task, create_agent_task), (upload_task, upload_agent_task),
                                  (execute_task, execute_agent_task)]:
            description = original.__doc__
            if http:
                description = description.replace("本会话", "此前").replace("模拟上传", "上传").replace("模拟执行", "执行")
                prefix = "通过 HTTP 调用地面站模拟服务，结果必须明确标注模拟。" if http.config.simulation else "通过 HTTP 调用地面站业务服务。"
            else:
                prefix = "本地任务模拟工具，不连接真实地面站或飞控。"
            server.tool(name=original.__name__, description=prefix + description)(direct)
    else:
        for tool in (create_task, upload_task, execute_task):
            server.tool()(tool)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-tools", action="store_true", help="向 Pi 提供简化业务参数，保留旧版默认接口")
    parser.add_argument("--config", type=Path, help="地面站 HTTP 服务 YAML 配置；指定后启用业务参数接口")
    parser.add_argument("--request-dir", type=Path, help="未确认 HTTP 请求的持久化目录")
    parser.add_argument("--check-config", action="store_true", help="只检查配置，不启动 MCP 或发送 HTTP 请求")
    parser.add_argument("--probe-config", action="store_true", help="用 HEAD 检查 HTTP 可达性，不调用业务工具")
    args = parser.parse_args()
    if args.probe_config:
        import json
        import httpx
        try:
            if not args.config:
                raise ValueError("请指定 --config")
            config = load_config(args.config)
            if not config.base_url:
                raise ValueError("请填写 ground_station.base_url")
            token = os.environ.get(config.token_env, "") if config.token_env else ""
            response = httpx.head(config.base_url + "/api/tasks", headers={"Authorization": f"Bearer {token}"} if token else {},
                                  timeout=min(config.timeout_seconds, 8), trust_env=False, follow_redirects=False)
            reachable = response.status_code in {200, 204, 405}
            print(json.dumps({"reachable": reachable, "status": response.status_code, "simulation": config.simulation,
                              "message": "HTTP 服务可达；未发送业务操作" if reachable else f"HTTP 返回 {response.status_code}，请核对地址或鉴权"}, ensure_ascii=False))
        except Exception:
            print(json.dumps({"reachable": False, "message": "HTTP 连接失败，请检查 YAML 地址、端口和服务状态"}, ensure_ascii=False))
        return
    if args.check_config:
        if not args.config:
            parser.error("--check-config 需要 --config")
        try:
            load_config(args.config)
        except Exception as exc:
            # No configuration values (including credentials) are echoed by this check.
            from pydantic import ValidationError
            message = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors(include_url=False)) if isinstance(exc, ValidationError) else str(exc)
            print(message, file=sys.stderr)
            raise SystemExit(1)
        print("配置有效")
        return
    state_path = os.environ.get("TASK_STATE_PATH")
    create_server(Path(os.environ.get("TASK_OUTPUT_DIR", str(OUTPUT_DIR))),
                  Path(state_path) if state_path else None, agent_tools=args.agent_tools or bool(args.config),
                  config_path=args.config, request_dir=args.request_dir).run(transport="stdio")


if __name__ == "__main__":
    main()
