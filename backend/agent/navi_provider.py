"""
NaviLLMProvider — 把 Navi 的 llm_client 适配成 AgentLoop 的 provider 接口。
loop.py 一行不用改，只需传入 NaviLLMProvider() 实例。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


# ── 数据结构定义（避免跨包依赖）────────────────────────────────────────────

@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None

    def to_openai_tool_call(self) -> dict[str, Any]:
        tc = {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }
        if self.provider_specific_fields:
            tc["provider_specific_fields"] = self.provider_specific_fields
        return tc


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    reasoning_content: str | None = None
    thinking_blocks: list[dict] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ── NaviLLMProvider ──────────────────────────────────────────────────────────

from providers.base import LLMProvider
from llm_constants import AGENT_DEFAULT_MAX_TOKENS, AGENT_DEFAULT_TEMPERATURE

# ── LLM 配置 TTL 缓存：避免每次 LLM 调用都触发 yaml 文件读取 ─────────────────
import time as _time

_cfg_cache: dict = {}
_cfg_cache_ts: float = 0.0
_CFG_TTL = 10.0  # 秒：配置热更新延迟最多 10 秒，可接受


def _get_cached_llm_cfg() -> dict:
    """带 TTL 的 LLM 配置缓存，10 秒内复用同一份配置，避免高频 IO。"""
    global _cfg_cache, _cfg_cache_ts
    now = _time.monotonic()
    if not _cfg_cache or now - _cfg_cache_ts > _CFG_TTL:
        from llm_client import _load_llm_cfg
        _cfg_cache = _load_llm_cfg()
        _cfg_cache_ts = now
    return _cfg_cache


class NaviLLMProvider(LLMProvider):
    """
    Navi LLM Provider。
    继承 LLMProvider 接口，loop.py 一行不用改。
    配置直接从 ~/.navi/config.yaml 读取，与 llm_client 共享同一套配置。
    配置读取有 10 秒 TTL 缓存，避免每次 LLM 调用都触发 yaml 文件 IO。
    """

    _CHAT_RETRY_DELAYS = (1, 2, 4)

    def get_default_model(self) -> str:
        return _get_cached_llm_cfg().get("text_model", "MiniMax-M2.5")

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """实现 LLMProvider 抽象方法，直接委托给 chat_with_retry。"""
        return await self.chat_with_retry(messages, tools, model, **kwargs)

    async def chat_with_retry(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        import asyncio

        last_error: Exception | None = None
        for delay in [*self._CHAT_RETRY_DELAYS, None]:
            try:
                # 在线程池跑同步 OpenAI 调用，不阻塞 asyncio 事件循环
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda m=messages, t=tools, mo=model: self._call_sync(m, t, mo, **kwargs),
                )
                return result
            except Exception as e:
                last_error = e
                if delay is not None:
                    logger.warning("[NaviLLM] 调用失败({}), {}s 后重试...", e, delay)
                    await asyncio.sleep(delay)

        raise RuntimeError(f"LLM 调用失败，已重试 {len(self._CHAT_RETRY_DELAYS)} 次: {last_error}")

    def _call_sync(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str | None,
        **kwargs,
    ) -> LLMResponse:
        from openai import OpenAI

        cfg = _get_cached_llm_cfg()
        api_key  = cfg.get("text_api_key", "")
        base_url = cfg.get("text_base_url", "")
        _model   = model or cfg.get("text_model", "")

        if not api_key:
            raise RuntimeError("未配置 text_api_key，请在「AI 模型」设置页填写")

        client = OpenAI(api_key=api_key, base_url=base_url)

        call_kwargs: dict[str, Any] = {
            "model":       _model,
            "messages":    messages,
            "temperature": kwargs.get("temperature", AGENT_DEFAULT_TEMPERATURE),
            "max_tokens":  kwargs.get("max_tokens", AGENT_DEFAULT_MAX_TOKENS),
        }
        if tools:
            call_kwargs["tools"] = tools
            call_kwargs["tool_choice"] = "auto"

        resp  = client.chat.completions.create(**call_kwargs)
        choice = resp.choices[0]
        msg    = choice.message

        # 解析 tool calls
        tool_calls: list[ToolCallRequest] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCallRequest(
                    id=tc.id or str(uuid.uuid4()),
                    name=tc.function.name,
                    arguments=args,
                ))

        # 思考型模型（MiniMax-M2.5/Kimi/DeepSeek-R1 等）会返回 reasoning_content。
        # 服务端硬性要求：下一轮调用必须把它原样塞回对应 assistant 消息，
        # 否则报 400 "The reasoning_content in the thinking mode must be passed back to the API"。
        reasoning_content = getattr(msg, "reasoning_content", None) or None
        thinking_blocks   = getattr(msg, "thinking_blocks", None) or None

        usage: dict[str, int] = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens":     getattr(resp.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
                "total_tokens":      getattr(resp.usage, "total_tokens", 0) or 0,
            }

        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        )