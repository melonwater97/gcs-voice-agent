"""L2: coordinate input, a replaceable proposal model, and registered MCP tools."""

import json
from contextlib import aclosing

from shared.config import AssistantConfig
from shared.contracts import describe_task
from inputs.sources import input_texts
from inference.models import DeepSeekModel, LocalModel, ProposalModel
from inference.prompts import SYSTEM_PROMPT
from integrations.mcp_client import MCPSession, ToolGateway
from shared.reporting import log, error_details
from .tools import DEFAULT_TOOLS, ToolRegistry

HISTORY_ROUNDS = 4  # 滑动窗口保留的对话轮数；每轮为 user + assistant (+ tool)
PENDING_MAX_CHARS = 200


def _history_entry(message: dict) -> dict:
    """Normalize a model reply for history: ensure role and tool_call ids."""
    entry = dict(message)
    entry.setdefault("role", "assistant")
    calls = entry.get("tool_calls")
    if isinstance(calls, list):
        entry["tool_calls"] = [
            {**call, "id": call.get("id") or f"call_{index}"}
            if isinstance(call, dict) else call
            for index, call in enumerate(calls)
        ]
    return entry


class Assistant:
    def __init__(self, model: ProposalModel, gateway: ToolGateway,
                 registry: ToolRegistry = DEFAULT_TOOLS):
        self.model = model
        self.gateway = gateway
        self.registry = registry
        self.tools: list[dict] = []
        self.allowed: set[str] = set()
        self.history: list[list[dict]] = []  # 每轮一组：user + assistant (+ tool)
        self.last_task: str | None = None    # 最近一次成功任务的可读描述
        self.pending: str | None = None      # 待补全的澄清目标

    async def prepare(self) -> None:
        discovered = await self.gateway.discover()
        self.tools = [tool for tool in discovered
                      if tool["function"]["name"] in self.registry.names]
        self.allowed = {tool["function"]["name"] for tool in self.tools}
        missing = self.registry.names - self.allowed
        if missing:
            raise ValueError("MCP 缺少已注册工具：" + ", ".join(sorted(missing)))
        log("L3", "发现 MCP 工具：" + ", ".join(sorted(self.allowed)))

    def _state_messages(self) -> list[dict]:
        if self.last_task is None and self.pending is None:
            return []
        lines = ["当前会话任务状态：",
                 f"- 上一个任务：{self.last_task}" if self.last_task else "- 尚未创建任务"]
        if self.pending:
            lines.append(f"- 待补全：{self.pending}")
        return [{"role": "system", "content": "\n".join(lines)}]

    def _build_messages(self, text: str) -> list[dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += self._state_messages()
        for round_messages in self.history:
            messages.extend(round_messages)
        if self.pending:
            messages.append({"role": "system", "content":
                f"用户正在回答之前的澄清（待补全：{self.pending}）；请把本句与待补全目标合并理解。"})
        messages.append({"role": "user", "content": text})
        if self.history:
            log("上下文", f"携带 {len(self.history)} 轮历史"
                + (f"；待补全：{self.pending}" if self.pending else ""))
        return messages

    def _remember_tool_outcome(self, content: str) -> None:
        """Attach the tool result to the current round, keeping the protocol valid."""
        if not self.history:
            return
        calls = self.history[-1][1].get("tool_calls") or []
        call_id = "call_0"
        if calls and isinstance(calls[0], dict):
            call_id = calls[0].get("id") or "call_0"
        self.history[-1].append({"role": "tool", "tool_call_id": call_id,
                                 "content": content})

    async def process_text(self, text: str) -> dict | None:
        text = text.strip()
        if not text:
            log("输入", "没有识别到文本，请重试")
            return None
        log("输入", text)
        messages = self._build_messages(text)
        message = await self.model.propose(messages, self.tools)
        log("L1", "提出动作：" + json.dumps(message, ensure_ascii=False))
        self.history.append([{"role": "user", "content": text},
                             _history_entry(message)])
        self.history = self.history[-HISTORY_ROUNDS:]
        try:
            invocation = self.registry.parse(message, self.allowed)
            if invocation is None:
                log("L2", "无工具调用，结束：" + (message.get("content") or "需要澄清"))
                self.pending = (message.get("content") or "待补全")[:PENDING_MAX_CHARS]
                return None
            arguments = invocation.arguments.model_dump()
            log("L2", "工具白名单与参数校验通过")
            log("L3", f"MCP call_tool: {invocation.spec.name} "
                + json.dumps(arguments, ensure_ascii=False))
            result = await self.gateway.call(invocation.spec.name, arguments)
            validated = invocation.validate_result(result)
        except Exception as exc:
            self._remember_tool_outcome(f"工具调用失败：{exc}")
            raise
        log("L3", "MCP 返回：" + json.dumps(result, ensure_ascii=False))
        log("结果", invocation.spec.describe_result(validated))
        self._remember_tool_outcome(json.dumps(result, ensure_ascii=False))
        payload = getattr(invocation.arguments, "payload", None)
        summary = describe_task(payload) if payload is not None else invocation.spec.name
        self.last_task = f"{validated.task_id}：{summary}"
        self.pending = None
        return validated.model_dump()


async def run(config: AssistantConfig) -> None:
    if config.deepseek:
        if not config.api_key:
            raise ValueError("使用 DeepSeek 需要设置环境变量 DEEPSEEK_API_KEY")
        log("L1", f"DeepSeek 云端模型：{config.model}")
        model = DeepSeekModel(api_key=config.api_key, model=config.model, url=config.url)
    else:
        log("L1", f"本地 oMLX 模型：{config.model}")
        model = LocalModel(config.model, config.url)
    log("地面站", config.gcs_url)
    async with MCPSession(config) as gateway:
        assistant = Assistant(model, gateway)
        await assistant.prepare()
        async with aclosing(input_texts(config)) as inputs:
            async for text in inputs:
                try:
                    await assistant.process_text(text)
                except Exception as exc:
                    if not config.mic:
                        raise
                    log("错误", error_details(exc))
