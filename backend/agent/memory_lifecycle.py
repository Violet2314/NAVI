"""
MemoryCompressionHook — 上下文压缩前的记忆抢救钩子

在 MessageConsolidator 触发 Token 压缩之前调用，是防止长对话信息
永久丢失的最后防线。

职责（仅此一件）：
  on_pre_compress() ← 从即将被截断的消息中提取用户事实 + 活动焦点，
                      写入 FactMemory 并返回补充文字并入压缩摘要。

设计原则：
  - 失败时只 log warning，绝不阻塞主流程
  - 其他生命周期事件（turn_start / session_end / memory_write）
    不在此处编排，由各自的调用方负责
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger("navi.memory.lifecycle")

if TYPE_CHECKING:
    pass


# ── 提取 Prompt ──────────────────────────────────────────────────────────────

_PRE_COMPRESS_FACTS_PROMPT = """\
请从以下对话片段中，提取关于用户的**新的**客观事实。

规则：
1. 只提取用户明确表达的信息，不推测
2. 每条 fact 是原子级的（一条只描述一个事实）
3. 分类：preference（偏好）/ status（当前状态）/ relationship（人际关系）/ skill（技能）/ habit（习惯）/ biographical（个人信息）
4. 只提取有长期价值的信息（"今天很累"不算，"在杭州工作"算）
5. 如果没有值得提取的事实，返回空数组

对话片段：
{conversation}

输出格式（严格 JSON 数组，不要输出任何其他内容）：
[
  {{"content": "事实内容", "category": "分类", "confidence": 0.85}}
]"""

_PRE_COMPRESS_FOCUS_PROMPT = """\
请从以下对话片段中，用一句话总结用户当前最关注的事情或活动焦点。

对话片段：
{conversation}

只输出一句话，不要任何解释。如果无法判断，输出空字符串。"""


class MemoryCompressionHook:
    """
    上下文压缩前的记忆抢救钩子（单一职责）。

    Usage:
        hook = MemoryCompressionHook()
        rescued_text = await hook.on_pre_compress(messages_about_to_be_dropped)
    """

    def __init__(self):
        self._initialized = False
        self._fact_memory = None
        self._llm_chat = None

    def _get_fact_memory(self):
        """懒加载 FactMemory（避免循环 import）。"""
        if self._fact_memory is None:
            try:
                from memory.fact_memory import get_fact_memory
                self._fact_memory = get_fact_memory()
            except Exception as e:
                logger.warning(f"[Lifecycle] FactMemory 不可用: {e}")
        return self._fact_memory

    def _get_llm_chat(self):
        """懒加载 llm_client.chat（避免循环 import）。"""
        if self._llm_chat is None:
            try:
                from llm_client import chat
                self._llm_chat = chat
            except Exception as e:
                logger.warning(f"[Lifecycle] llm_client 不可用: {e}")
        return self._llm_chat

    # ── 核心钩子：压缩前抢救 ──────────────────────────────────────────────────

    async def on_pre_compress(self, messages: list[dict]) -> str:
        """
        在上下文压缩前调用，是防止长对话信息永久丢失的最后防线。

        执行：
          1. 从即将被压缩的消息中提取新用户事实 → 写入 FactMemory
          2. 提取本轮活动焦点 → 附加到压缩摘要

        Returns:
            需要并入压缩摘要的补充文字（空字符串表示无需补充）。
        """
        if not messages:
            return ""

        rescued_parts = []

        # 1. 提取并保存用户事实（写入 FactMemory，持久化）
        new_facts_text = await self._extract_and_save_facts(messages)
        if new_facts_text:
            rescued_parts.append(f"[压缩前抢救的用户事实]\n{new_facts_text}")

        # 2. 提取活动焦点（附加到摘要，帮助下次 LLM 快速恢复上下文）
        focus = await self._extract_focus(messages)
        if focus:
            rescued_parts.append(f"[本轮用户活动焦点] {focus}")

        result = "\n\n".join(rescued_parts)
        if result:
            logger.info(f"[Lifecycle] on_pre_compress: 抢救了 {len(rescued_parts)} 项信息")
        return result

    async def _extract_and_save_facts(self, messages: list[dict]) -> str:
        """从消息中提取用户事实并写入 FactMemory。返回提取到的事实文本（用于日志）。"""
        chat = self._get_llm_chat()
        fact_mem = self._get_fact_memory()
        if not chat or not fact_mem:
            return ""

        conversation = _format_messages_for_prompt(messages)
        if not conversation.strip():
            return ""

        try:
            from llm_constants import MEMORY_EXTRACTION_MAX_TOKENS, MEMORY_EXTRACTION_TEMPERATURE
            raw = chat(
                messages=[{"role": "user", "content": _PRE_COMPRESS_FACTS_PROMPT.replace("{conversation}", conversation)}],
                system="你是一个精确的信息提取系统。只输出 JSON 数组，不要输出任何解释。",
                temperature=MEMORY_EXTRACTION_TEMPERATURE,
                max_tokens=MEMORY_EXTRACTION_MAX_TOKENS,
            )
            import json
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            facts = json.loads(raw)
            if not isinstance(facts, list) or not facts:
                return ""

            # 写入 FactMemory
            added = fact_mem.add_facts(facts)
            if added:
                lines = [f"  - {f['content']}" for f in facts[:added]]
                logger.info(f"[Lifecycle] on_pre_compress: 写入 {added} 条新事实到 FactMemory")
                return "\n".join(lines)

        except Exception as e:
            logger.warning(f"[Lifecycle] 事实提取失败: {e}")

        return ""

    async def _extract_focus(self, messages: list[dict]) -> str:
        """从消息中提取用户当前活动焦点（一句话）。"""
        chat = self._get_llm_chat()
        if not chat:
            return ""

        conversation = _format_messages_for_prompt(messages, max_chars=800)
        if not conversation.strip():
            return ""

        try:
            from llm_constants import MEMORY_CONTRADICTION_MAX_TOKENS, MEMORY_CONTRADICTION_TEMPERATURE
            result = chat(
                messages=[{"role": "user", "content": _PRE_COMPRESS_FOCUS_PROMPT.replace("{conversation}", conversation)}],
                system="你是一个信息提取助手。只输出一句话。",
                temperature=MEMORY_CONTRADICTION_TEMPERATURE,
                max_tokens=min(MEMORY_CONTRADICTION_MAX_TOKENS, 100),
            )
            return result.strip()
        except Exception as e:
            logger.warning(f"[Lifecycle] 焦点提取失败: {e}")
            return ""


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _format_messages_for_prompt(messages: list[dict], max_chars: int = 2000) -> str:
    """将消息列表格式化为紧凑对话文本，用于 LLM 提示词。"""
    lines = []
    total = 0
    for m in messages:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content or role not in ("user", "assistant"):
            continue
        label = "用户" if role == "user" else "Navi"
        # 截断过长的单条消息
        snippet = content[:400] if len(content) > 400 else content
        line = f"{label}: {snippet}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


# ── 单例 ──────────────────────────────────────────────────────────────────────

_lifecycle_manager: Optional[MemoryCompressionHook] = None


def get_lifecycle_manager() -> MemoryCompressionHook:
    """获取全局单例 MemoryCompressionHook（名称保持向后兼容，调用方无需改动）。"""
    global _lifecycle_manager
    if _lifecycle_manager is None:
        _lifecycle_manager = MemoryCompressionHook()
    return _lifecycle_manager
