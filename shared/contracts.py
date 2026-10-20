"""Shared task contracts for Agent validation and MCP simulation.

任务契约按「注册表 + 判别联合」组织，新增类型只需“定义模型 + 注册一条”，
Agent 参数校验、MCP Schema 与模拟任务操作共用这些模型：

1. 任务类型 TASK_TYPES：task_type 判别，注册任务级模型；
2. 放飞模式 LAUNCH_MODES：mode 判别，注册放飞前出任务的参数模型；
3. 编队类型 FORMATION_TYPES：formation_type 判别，注册编队参数模型；
工具动作由 assistant/tools.py 注册；MSG_TYPES 标识 Agent 对外响应的来源。

任务状态按 TASK_STATUS_TRANSITIONS 流转：draft（创建）→ uploaded（保存并上传）
→ executing（执行）；completed / aborted 为后续执行结果预留。
"""

from typing import Annotated, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Position(StrictModel):
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=False,
                             description="WGS84 经度，十进制度，东经为正")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=False,
                            description="WGS84 纬度，十进制度，北纬为正")


def format_position(position: Position) -> str:
    return f"({position.longitude:g}°, {position.latitude:g}°)"


def discriminated(models: tuple[type[BaseModel], ...], field: str):
    """按判别字段构建联合类型；注册表只有一条时直接返回该模型。"""
    if len(models) == 1:
        return models[0]
    return Annotated[Union[models], Field(discriminator=field)]


# ---- 第三层：编队类型注册表。新增编队：定义参数模型（formation_type 判别 + describe）后注册。 ----
class CFormation(StrictModel):
    formation_type: Literal["C型"]
    open_angle: float = Field(gt=0, le=360, strict=False, description="打开角度，单位 度")
    radius: float = Field(gt=0, strict=False, description="半径，单位 m")
    position: Position = Field(description="展开位置")

    def describe(self) -> str:
        return (f"C型编队（打开角度{self.open_angle:g}°，半径{self.radius:g}m，"
                f"展开位置{format_position(self.position)}）")


class UFormation(StrictModel):
    formation_type: Literal["U型"]
    formation_length: float = Field(gt=0, strict=False, description="编队长度，单位 m")
    formation_width: float = Field(gt=0, strict=False, description="编队宽度，单位 m")
    position: Position = Field(description="展开位置")

    def describe(self) -> str:
        return (f"U型编队（长度{self.formation_length:g}m，宽度{self.formation_width:g}m，"
                f"展开位置{format_position(self.position)}）")


FORMATION_TYPES: dict[str, type[StrictModel]] = {"C型": CFormation, "U型": UFormation}
Formation = discriminated(tuple(FORMATION_TYPES.values()), "formation_type")


# ---- 第二层：放飞模式注册表。新增模式：定义参数模型（mode 判别 + describe）后注册。 ----
class TakeoffInPlace(StrictModel):
    mode: Literal["原地起飞"]
    takeoff_altitude: float = Field(gt=0, strict=False, description="起飞高度，单位 m")

    def describe(self) -> str:
        return f"原地起飞（起飞高度{self.takeoff_altitude:g}m）"


class DeployAtPosition(StrictModel):
    mode: Literal["在指定位置展开"]
    takeoff_altitude: float = Field(gt=0, strict=False, description="起飞高度，单位 m")
    formation: Formation = Field(description="编队类型与编队参数")

    def describe(self) -> str:
        return (f"在指定位置展开（起飞高度{self.takeoff_altitude:g}m，"
                f"{self.formation.describe()}）")


LAUNCH_MODES: dict[str, type[StrictModel]] = {
    "原地起飞": TakeoffInPlace, "在指定位置展开": DeployAtPosition,
}
LaunchMode = discriminated(tuple(LAUNCH_MODES.values()), "mode")


# ---- 第一层：任务类型注册表。新增任务类型：定义任务模型（task_type 判别 + describe）后注册。 ----
class PreLaunchTask(StrictModel):
    task_type: Literal["放飞前出任务"]
    task_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                               max_length=64)] | None = Field(
        default=None, description="任务名称，1–64 字；用户指定时原样填写，未指定可省略，由服务生成")
    parameters: LaunchMode = Field(description="放飞模式及其参数")

    def describe(self) -> str:
        name = f"「{self.task_name}」" if self.task_name else ""
        return f"{self.task_type}{name}：{self.parameters.describe()}"


TASK_TYPES: dict[str, type[StrictModel]] = {"放飞前出任务": PreLaunchTask}
Task = discriminated(tuple(TASK_TYPES.values()), "task_type")


def named_task(task, task_id: str):
    """Allocate a stable readable name without inventing a user-provided name."""
    if task.task_name is not None:
        return task
    return task.model_copy(update={"task_name": f"{task.task_type}-{task_id[5:13]}"})


def describe_task(task) -> str:
    return task.describe()


# ---- 创建工具的结构化任务参数。 ----
class CreateTaskPayload(StrictModel):
    action: Literal["create_task"]
    task: Task = Field(description="要创建的任务")


TaskId = Annotated[str, Field(min_length=6, max_length=64, pattern=r"^TASK-[0-9a-f]+$")]


class CreateTaskArguments(StrictModel):
    payload: CreateTaskPayload


class UploadTaskArguments(StrictModel):
    task_id: TaskId


class ExecuteTaskArguments(StrictModel):
    task_id: TaskId


# ---- 统一业务消息：HTTP 与 MCP 的业务数据均使用 header + payload。 ----
MSG_TYPES = {
    "VOICE_INPUT": "地面站提交语音", "AGENT_INPUT": "地面站提交文本",
    "ASR_OUTPUT": "语音转写完成", "AGENT_OUTPUT": "口令处理结果",
    "RECORD_START": "开始服务端录音", "RECORD_STOP": "结束服务端录音",
    "RECORD_STATUS": "录音进度查询", "RECORD_START_OUTPUT": "录音监听状态",
    "RECORD_STATUS_OUTPUT": "录音进度快照",
    "STATE_QUERY": "查询状态", "STATE_OUTPUT": "状态快照",
    "HEALTH_QUERY": "健康查询", "HEALTH_OUTPUT": "健康状态",
    "CREATE_TASK": "创建工具请求", "UPLOAD_TASK": "上传工具请求",
    "EXECUTE_TASK": "执行工具请求", "TASK_RESULT": "工具成功结果",
    "ERROR": "请求错误",
}
MessageType = Literal[tuple(MSG_TYPES)]
MsgId = Annotated[str, Field(
    pattern=r"^MSG-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    description="MSG-<UUID>，例如 MSG-550e8400-e29b-41d4-a716-446655440000")]


class MessageEndpoint(StrictModel):
    type: Literal["FRONT", "BACKGROUND"]
    id: str = Field(min_length=1, max_length=64)


class MessageHeader(StrictModel):
    msg_id: MsgId
    msg_type: MessageType
    version: Literal["1.0.0"]
    src: MessageEndpoint
    dst: MessageEndpoint


PayloadT = TypeVar("PayloadT")


class Message(StrictModel, Generic[PayloadT]):
    header: MessageHeader
    payload: PayloadT


class VoiceInput(StrictModel):
    audio_format: Literal["wav"]
    audio_base64: str = Field(min_length=1)


class TextInput(StrictModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EmptyPayload(StrictModel):
    pass


class TaskIdPayload(StrictModel):
    task_id: TaskId


class DraftTaskResult(StrictModel):
    status: Literal["draft"]
    task_id: TaskId
    task: Task


class UploadTaskResult(StrictModel):
    status: Literal["uploaded"]
    task_id: TaskId
    task: Task
    file: str = Field(min_length=1)


class ExecuteTaskResult(StrictModel):
    status: Literal["executing"]
    task_id: TaskId


# ---- 地面站只接收本次操作结果；完整状态另行查询。 ----
ToolName = Literal["create_task", "upload_task", "execute_task"]


class AgentTaskReply(StrictModel):
    type: Literal["task_result"]
    tool: ToolName
    task_id: TaskId
    status: Literal["draft", "uploaded", "executing"]
    task: Task


class AgentClarifyReply(StrictModel):
    type: Literal["clarify"]
    text: str


class AgentErrorReply(StrictModel):
    type: Literal["error"]
    text: str
    tool: ToolName | None = None


AgentReply = Annotated[Union[AgentTaskReply, AgentClarifyReply, AgentErrorReply],
                       Field(discriminator="type")]


# ---- 生命周期状态机：扩展新状态时在此登记允许的转移。 ----
TASK_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("uploaded",),
    "uploaded": ("executing",),
    "executing": ("completed", "aborted"),  # completed/aborted 为后续执行结果预留
}


def can_transition(current: str, target: str) -> bool:
    return target in TASK_STATUS_TRANSITIONS.get(current, ())


def result_task(result) -> object | None:
    """从各类任务结果中取出任务内容，便于统一生成状态摘要。"""
    task = getattr(result, "task", None)
    if task is not None:
        return task
    return None
