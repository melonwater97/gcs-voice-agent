"""统一业务消息信封：header + payload，UUID 消息编号。"""

from uuid import uuid4

from shared.contracts import Message, MessageHeader, REPLY_STATUS

FRONT = {"type": "FRONT", "id": "GS-01"}
AGENT = {"type": "BACKGROUND", "id": "AGENT-01"}
MCP = {"type": "BACKGROUND", "id": "MCP-01"}


def next_msg_id() -> str:
    return f"MSG-{uuid4()}"


def make_message(msg_type: str, payload: dict, *, src: dict = AGENT,
                 dst: dict = FRONT, request: MessageHeader | None = None,
                 msg_id: str | None = None) -> dict:
    if request is not None:
        dst = request.src.model_dump()
    return Message[dict](header=MessageHeader(
        msg_id=msg_id or next_msg_id(), msg_type=msg_type, version="1.0.0",
        src=src, dst=dst), payload=payload).model_dump()


def agent_output(payload: dict, *, request: MessageHeader | None = None) -> dict:
    """装配 AGENT_OUTPUT；业务错误使用非 200 code，不设置 payload.type。"""
    if payload.get("type") == "error":
        return agent_error(REPLY_STATUS["error"][0], payload.get("text", "操作失败"),
                           request=request)
    code, message = REPLY_STATUS[payload["type"]]
    return make_message("AGENT_OUTPUT",
                        {"code": code, "message": message, **payload}, request=request)


def agent_error(status: int, message: str, *, request: MessageHeader | None = None) -> dict:
    """HTTP 层错误：payload 只放 code（HTTP 状态码）+ message。"""
    return make_message("AGENT_OUTPUT", {"code": status, "message": message},
                        request=request)


def check_route(header: MessageHeader, msg_type: str, src_type: str, dst: dict) -> None:
    if header.msg_type != msg_type:
        raise ValueError(f"msg_type 应为 {msg_type}")
    if header.src.type != src_type or header.dst.model_dump() != dst:
        raise ValueError("消息 src / dst 与调用方向不符")
