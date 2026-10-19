"""L2 tool policy: registered contracts validate proposals and actual results."""

import json
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from shared.contracts import (CreateTaskArguments, DraftTaskResult, ExecuteTaskArguments,
                              ExecuteTaskResult, UploadTaskArguments, UploadTaskResult,
                              describe_task, named_task)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    arguments_model: type[BaseModel]
    result_model: type[BaseModel]
    verify_result: Callable[[BaseModel, BaseModel], None]
    describe_result: Callable[[BaseModel], str]


@dataclass(frozen=True)
class Invocation:
    spec: ToolSpec
    arguments: BaseModel

    def validate_result(self, result: dict) -> BaseModel:
        validated = self.spec.result_model.model_validate(result)
        self.spec.verify_result(self.arguments, validated)
        return validated


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]):
        self._specs = {spec.name: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("工具名称不能重复")

    @property
    def names(self) -> set[str]:
        return set(self._specs)

    def parse(self, message: dict, discovered: set[str]) -> Invocation | None:
        if not isinstance(message, dict):
            raise ValueError("模型消息必须是对象")
        calls = message.get("tool_calls")
        if calls is None:
            return None
        if not isinstance(calls, list):
            raise ValueError("tool_calls 必须是列表")
        if not calls:
            return None
        if len(calls) != 1:
            raise ValueError("每条口令只接受一个工具调用")
        call = calls[0]
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            raise ValueError("工具调用缺少 function 对象")
        name = function.get("name")
        if not isinstance(name, str) or name not in discovered or name not in self._specs:
            raise ValueError("拒绝未知或不属于当前流程的工具")
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        spec = self._specs[name]
        return Invocation(spec, spec.arguments_model.model_validate(arguments))


def verify_created_task(arguments: CreateTaskArguments, result: DraftTaskResult) -> None:
    if result.task != named_task(arguments.payload.task, result.task_id):
        raise ValueError("返回的任务与调用建议不一致")


def describe_created_task(result: DraftTaskResult) -> str:
    return (f"已创建{describe_task(result.task)}，任务编号：{result.task_id}；"
            "当前为草稿，尚未保存上传，未开始飞行。")


def verify_uploaded_task(arguments: UploadTaskArguments, result: UploadTaskResult) -> None:
    if result.task_id != arguments.task_id:
        raise ValueError("返回任务编号不一致")


def describe_uploaded_task(result: UploadTaskResult) -> str:
    return (f"已保存并模拟上传{describe_task(result.task)}，"
            f"任务编号：{result.task_id}；任务文件已保存在 Agent 侧，未开始飞行。")


def verify_executed_task(arguments: ExecuteTaskArguments, result: ExecuteTaskResult) -> None:
    if result.task_id != arguments.task_id:
        raise ValueError("返回任务编号不一致")


def describe_executed_task(result: ExecuteTaskResult) -> str:
    return f"任务 {result.task_id} 进入模拟执行状态。"


DEFAULT_TOOLS = ToolRegistry([
    ToolSpec("create_task", CreateTaskArguments, DraftTaskResult,
             verify_created_task, describe_created_task),
    ToolSpec("upload_task", UploadTaskArguments, UploadTaskResult,
             verify_uploaded_task, describe_uploaded_task),
    ToolSpec("execute_task", ExecuteTaskArguments, ExecuteTaskResult,
             verify_executed_task, describe_executed_task),
])
