"""Shared task contract: L2 and L4 validate the same payload; L3 publishes it."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.config import INTERFACE_FILE


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Position(StrictModel):
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False,
                             description="WGS84 经度，十进制度，东经为正")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False,
                            description="WGS84 纬度，十进制度，北纬为正")


class Area(StrictModel):
    vertices: list[Position] = Field(min_length=3,
        description="WGS84 多边形边界，按顺序提供至少三个不同顶点，不重复首点")

    @model_validator(mode="after")
    def distinct_vertices(self):
        points = {(point.longitude, point.latitude) for point in self.vertices}
        if len(points) != len(self.vertices):
            raise ValueError("区域顶点不能重复，至少需要三个不同顶点")
        return self


class TakeoffInPlace(StrictModel):
    mode: Literal["原地起飞"]


class DeployAtPosition(StrictModel):
    mode: Literal["在指定位置展开"]
    position: Position


class DeployInArea(StrictModel):
    mode: Literal["在指定区域展开"]
    area: Area


class PreLaunchTask(StrictModel):
    task_type: Literal["放飞前出任务"]
    parameters: Annotated[
        TakeoffInPlace | DeployAtPosition | DeployInArea, Field(discriminator="mode")
    ]


class FormationParameters(StrictModel):
    formation_type: Literal["C型", "U型"]


class FormationTask(StrictModel):
    task_type: Literal["编队飞行任务"]
    parameters: FormationParameters


class CreateTaskPayload(StrictModel):
    action: Literal["create_task"]
    task: Annotated[PreLaunchTask | FormationTask, Field(discriminator="task_type")]


class CreateTaskArguments(StrictModel):
    payload: CreateTaskPayload


class MessageEndpoint(StrictModel):
    type: str = Field(min_length=1)
    id: str = Field(min_length=1)


class MessageHeader(StrictModel):
    msg_id: str = Field(min_length=1)
    msg_type: Literal["PRE_PLAN"]
    version: Literal["1.0.0"]
    src: MessageEndpoint
    dst: MessageEndpoint


class TaskMessage(StrictModel):
    header: MessageHeader
    payload: CreateTaskPayload


class CreateTaskResult(StrictModel):
    status: Literal["created"]
    task_id: str = Field(min_length=1)
    message: TaskMessage
    file: str = Field(min_length=1)


def describe_task(payload: CreateTaskPayload) -> str:
    task = payload.task
    if isinstance(task, FormationTask):
        return f"{task.task_type}（{task.parameters.formation_type}）"
    return f"{task.task_type}（{task.parameters.mode}）"
