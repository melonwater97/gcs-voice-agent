"""统一业务消息信封：header + payload，UUID 消息编号。"""

from uuid import uuid4

from shared.contracts import Message, MessageHeader

FRONT = {"type": "FRONT", "id": "GS-01"}
AGENT = {"type": "BACKGROUND", "id": "AGENT-01"}
MCP = {"type": "BACKGROUND", "id": "MCP-01"}


def next_msg_id() -> str:
    return f"MSG-{uuid4()}"


def make_message(msg_type: str, payload: dict, *, src: dict = AGENT,
                 dst: dict = FRONT, request: MessageHeader | None = None,
                 msg_id: str | None = None, ref_msg_id: str | None = None) -> dict:
    if request is not None:
        dst = request.src.model_dump()
        ref_msg_id = request.msg_id
    return Message[dict](header=MessageHeader(
        msg_id=msg_id or next_msg_id(), msg_type=msg_type, version="1.0.0",
        src=src, dst=dst, ref_msg_id=ref_msg_id), payload=payload).model_dump()


def check_route(header: MessageHeader, msg_type: str, src_type: str, dst: dict) -> None:
    if header.msg_type != msg_type:
        raise ValueError(f"msg_type 应为 {msg_type}")
    if header.src.type != src_type or header.dst.model_dump() != dst:
        raise ValueError("消息 src / dst 与调用方向不符")
