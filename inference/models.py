"""Replaceable inference providers; models propose actions without executing them."""

from typing import Protocol

import httpx

from shared.config import (DEFAULT_MODEL, DEFAULT_URL, DEEPSEEK_MODEL,
                           DEEPSEEK_URL)


class ProposalModel(Protocol):
    async def propose(self, messages: list[dict], tools: list[dict]) -> dict: ...


def _validated_message(body) -> dict:
    if not isinstance(body, dict):
        raise ValueError("模型返回结构无效：需要 choices[0].message")
    choices = body.get("choices")
    if (not isinstance(choices, list) or not choices
            or not isinstance(choices[0], dict)
            or not isinstance(choices[0].get("message"), dict)):
        raise ValueError("模型返回结构无效：需要 choices[0].message")
    return choices[0]["message"]


class LocalModel:
    def __init__(self, model: str = DEFAULT_MODEL, url: str = DEFAULT_URL,
                 *, transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self.url = url
        self.transport = transport

    async def propose(self, messages: list[dict], tools: list[dict]) -> dict:
        try:
            async with httpx.AsyncClient(timeout=180, trust_env=False,
                                         transport=self.transport) as http:
                response = await http.post(self.url, json={
                    "model": self.model,
                    "messages": messages,
                    "tools": tools, "tool_choice": "auto", "temperature": 0,
                    "max_tokens": 2048,
                    # 每条口令只执行一次调用；显式声明禁止并行工具调用，
                    # 让本地服务端（如 llama.cpp）在生成阶段就约束为单次调用。
                    "parallel_tool_calls": False,
                })
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"oMLX 请求失败，请检查本地模型服务：{exc}") from exc
        return _validated_message(body)


class DeepSeekModel:
    def __init__(self, api_key: str, model: str = DEEPSEEK_MODEL,
                 url: str = DEEPSEEK_URL,
                 *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.model = model
        self.url = url
        self.transport = transport

    async def propose(self, messages: list[dict], tools: list[dict]) -> dict:
        try:
            async with httpx.AsyncClient(timeout=60, trust_env=False,
                                         transport=self.transport) as http:
                response = await http.post(self.url, json={
                    "model": self.model,
                    "messages": messages,
                    "tools": tools, "tool_choice": "auto", "temperature": 0,
                    "max_tokens": 2048,
                    "parallel_tool_calls": False,
                }, headers={"Authorization": f"Bearer {self.api_key}"})
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"DeepSeek 请求失败，请检查 API Key 与网络：{exc}") from exc
        return _validated_message(body)
