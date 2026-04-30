"""
generator.py — 消息生成器：调用 LLM 生成符合 Soul 人格的主动消息

关键：不是模板填充，而是让 LLM 根据
Soul + 上下文 + 策略类型 自由生成。

这是整个主动对话引擎中唯一消耗 token 的步骤。
"""
import json
import logging
import re
from datetime import datetime
from typing import Optional

from proactive.triggers import Trigger
from proactive.strategies import StrategySelector, STRATEGY_INSTRUCTIONS
from proactive.constants import GENERATOR_MAX_TOKENS, GENERATOR_TEMPERATURE

logger = logging.getLogger("navi.proactive.generator")


class ProactiveMessageGenerator:
    """
    调用 LLM 生成符合 Soul 人格的主动消息。

    Prompt 结构：
    - System: soul.md 内容 + 主动对话专用指令
    - User: 触发上下文 + 策略指令
    """

    def __init__(self, llm_provider, soul_content: str = ""):
        self.llm_provider = llm_provider
        self.soul_content = soul_content

    async def generate(
        self,
        strategy: str,
        trigger: Trigger,
        context: dict,
        soul_content: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        """
        生成主动消息。

        返回 (消息文本, 情绪标签)。
        情绪标签用于 Live2D 表情切换。
        """
        soul = soul_content or self.soul_content
        strategy_instruction = STRATEGY_INSTRUCTIONS.get(strategy, "你现在要主动和用户说话。")

        # ── 构造 System Prompt ──
        system_prompt = self._build_system_prompt(soul, strategy_instruction)

        # ── 构造 User Prompt ──
        user_prompt = self._build_user_prompt(trigger, context)

        # ── 调用 LLM ──
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            response = await self._call_llm(messages)

            # 解析情绪标签
            emotion, clean_text = self._parse_emotion(response)
            
            logger.info(
                f"Generated proactive message: strategy={strategy}, "
                f"emotion={emotion}, text={clean_text[:50]}..."
            )
            return clean_text, emotion

        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            # 降级：返回一个安全的默认消息
            return self._fallback_message(strategy, trigger), None

    def _build_system_prompt(self, soul: str, strategy_instruction: str) -> str:
        """构造系统提示，注入 InsightsEngine 的用户行为洞察（Phase 2）。"""

        # ── Phase 2: 注入 InsightsEngine 的用户洞察 ──────────────────────
        insights_block = ""
        try:
            from agent.insights_engine import get_insights_engine
            engine = get_insights_engine()
            if engine:
                report = engine.generate(days=14)
                summary = engine.format_summary(report)
                if summary:
                    insights_block = f"\n\n[用户行为洞察] {summary}"
        except Exception:
            pass  # 非致命，降级忽略

        proactive_rules = (
            "\n\n--- 主动对话模式 ---\n"
            f"{strategy_instruction}\n\n"
            "重要规则：\n"
            "- 说话要简短自然，1-3 句话就好，不要超过 50 个字\n"
            "- 不要解释你为什么突然说话\n"
            "- 不要用 \"我注意到\" \"我发现\" 这种监控感强的开头\n"
            "- 像朋友/伴侣自然地说话\n"
            "- 完全按照你的性格和说话风格来\n"
            "- 在回复末尾用 [EMOTION:xxx] 标注你的情绪，可选值：\n"
            "  happy, sad, angry, surprised, thinking, normal\n"
        )
        return soul + insights_block + proactive_rules

    def _build_user_prompt(self, trigger: Trigger, context: dict) -> str:
        """构造用户提示（触发上下文）"""
        now = datetime.now()
        parts = [
            f"当前时间: {now.strftime('%Y-%m-%d %H:%M')}（{'周末' if now.weekday() >= 5 else '工作日'}）",
        ]

        # 添加触发上下文
        trigger_ctx = trigger.context or {}
        if trigger.type == "long_work":
            parts.append(f"用户已经连续工作了 {trigger_ctx.get('work_minutes', '?')} 分钟")
        elif trigger.type == "slacking":
            parts.append(
                f"用户在 {trigger_ctx.get('app', '某应用')} 上"
                f"摸了 {trigger_ctx.get('duration_min', '?')} 分钟鱼"
            )
        elif trigger.type == "blacklist":
            parts.append(f"用户打开了 {trigger_ctx.get('app', '某应用')}（黑名单应用）")
        elif trigger.type == "late_night":
            parts.append(f"现在是深夜 {trigger_ctx.get('hour', '')}:{trigger_ctx.get('minute', ''):02d}")
        elif trigger.type == "morning_greet":
            parts.append("用户刚开始今天的电脑使用")
        elif trigger.type == "evening_greet":
            parts.append("用户到了傍晚时间")
        elif trigger.type == "idle":
            parts.append(f"用户已经 {trigger_ctx.get('idle_minutes', '?')} 分钟没有活动了")
        elif trigger.type == "periodic_chat":
            parts.append(f"已经 {trigger_ctx.get('hours_since_chat', '?')} 小时没有聊天了")

        # 如果有合并的其他触发
        also = trigger_ctx.get("also_triggered", [])
        if also:
            other_types = [t["type"] for t in also]
            parts.append(f"同时还检测到: {', '.join(other_types)}")

        # 添加最近活动摘要
        activities = context.get("recent_activities", [])
        if activities:
            recent = activities[:5]
            act_summary = []
            for act in recent:
                app = act[0] if act else "?"
                title = (act[1] if len(act) > 1 else "")[:30]
                cat = act[2] if len(act) > 2 else "?"
                dur = act[3] if len(act) > 3 else 0
                act_summary.append(f"  - {app} ({cat}, {int(dur/60)}分钟): {title}")
            parts.append("最近活动:\n" + "\n".join(act_summary))

        return "\n".join(parts)

    def _parse_emotion(self, text: str) -> tuple[Optional[str], str]:
        """从 LLM 输出中提取情绪标签并清理"""
        emotion_match = re.search(r'\[EMOTION:(\w+)\]', text, re.IGNORECASE)
        emotion = emotion_match.group(1).lower() if emotion_match else None
        clean = re.sub(r'\s*\[EMOTION:\w+\]', '', text, flags=re.IGNORECASE).strip()
        return emotion, clean

    def _fallback_message(self, strategy: str, trigger: Trigger) -> str:
        """LLM 调用失败时的降级消息"""
        fallbacks = {
            "remind": "该休息一下了~",
            "care": "还好吗？",
            "tease": "又在摸鱼？",
            "greet": "嗨~",
            "chat": "在干嘛呢？",
        }
        return fallbacks.get(strategy, "嗨~")

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """调用 LLM Provider"""
        # 适配 NaviLLMProvider 的接口
        if hasattr(self.llm_provider, 'chat'):
            # 新版 provider — 返回可能是 LLMResponse 对象
            response = await self.llm_provider.chat(messages)
            raw = response.content if hasattr(response, 'content') else (
                response if isinstance(response, str) else str(response)
            )
            # 剥离 <think>...</think> 标签（deepseek/minimax 等模型的思考链）
            raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
            return raw
        elif hasattr(self.llm_provider, 'complete'):
            # 旧版 provider
            response = await self.llm_provider.complete(messages)
            raw = response.content if hasattr(response, 'content') else str(response)
            raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
            return raw
        else:
            # 最通用的方式：用 openai 兼容接口 + Navi 配置
            from openai import AsyncOpenAI
            from config.navi import get_config
            cfg = get_config()
            llm_cfg = cfg.llm or {}
            client = AsyncOpenAI(
                api_key=llm_cfg.get("text_api_key", ""),
                base_url=llm_cfg.get("text_base_url", ""),
            )
            response = await client.chat.completions.create(
                model=llm_cfg.get("text_model", "deepseek-chat"),
                messages=messages,  # type: ignore[arg-type]
                max_tokens=GENERATOR_MAX_TOKENS,
                temperature=GENERATOR_TEMPERATURE,
            )
            return response.choices[0].message.content or ""
