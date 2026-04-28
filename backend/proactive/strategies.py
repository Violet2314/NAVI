"""
strategies.py — 策略库：选择主动行为类型

根据触发类型 + Soul 场景反应，选择最合适的主动行为策略。
"""
import logging
from typing import Optional

from proactive.triggers import Trigger, TriggerType

logger = logging.getLogger("navi.proactive.strategies")


class StrategyType:
    """主动行为策略常量"""
    CARE      = "care"        # 关怀：温柔地关心用户状态
    REMIND    = "remind"      # 提醒：该休息了/该喝水了
    TEASE     = "tease"       # 吐槽：发现摸鱼时按性格吐槽
    SHARE     = "share"       # 分享：推荐可能感兴趣的内容
    SUMMARIZE = "summarize"   # 总结：日报/周报
    FOLLOW_UP = "follow_up"   # 跟进：之前提到的待办事项
    GREET     = "greet"       # 问候：早安/晚安
    CHAT      = "chat"        # 闲聊：找话题聊天


# ── 触发器 → 候选策略映射 ──────────────────────────────────────────────

TRIGGER_STRATEGY_MAP: dict[str, list[str]] = {
    TriggerType.LONG_WORK:     [StrategyType.REMIND, StrategyType.CARE],
    TriggerType.SLACKING:      [StrategyType.TEASE, StrategyType.REMIND],
    TriggerType.BLACKLIST:     [StrategyType.TEASE, StrategyType.REMIND],
    TriggerType.IDLE:          [StrategyType.CARE, StrategyType.CHAT],
    TriggerType.LATE_NIGHT:    [StrategyType.CARE, StrategyType.REMIND],
    TriggerType.MORNING_GREET: [StrategyType.GREET],
    TriggerType.EVENING_GREET: [StrategyType.GREET, StrategyType.SUMMARIZE],
    TriggerType.TASK_FOLLOW_UP:[StrategyType.FOLLOW_UP],
    TriggerType.PERIODIC_CHAT: [StrategyType.CHAT, StrategyType.SHARE],
}

# 每种策略的 LLM 指令提示（注入到 generator 的 prompt 中）
STRATEGY_INSTRUCTIONS: dict[str, str] = {
    StrategyType.CARE: (
        "你现在要主动关心用户的状态。"
        "语气温暖但不做作，简短一两句话就好。"
        "不要解释你为什么突然关心他。"
    ),
    StrategyType.REMIND: (
        "你现在要提醒用户该休息了。"
        "可以用你的性格方式表达（吐槽、温柔、撒娇都行），但核心是提醒。"
        "不要说教，简短有力。"
    ),
    StrategyType.TEASE: (
        "你现在要吐槽/调侃用户。"
        "要好笑但不伤人，符合你和用户的关系。"
        "不要太长，一两句精准吐槽最好。"
    ),
    StrategyType.SHARE: (
        "你现在要主动分享一个用户可能感兴趣的信息或话题。"
        "基于你知道的用户兴趣来选择话题。"
        "自然地引出，不要像推销。"
    ),
    StrategyType.SUMMARIZE: (
        "你现在要为用户做一个简短的活动总结。"
        "概括今天的工作/学习情况，语气轻松。"
    ),
    StrategyType.FOLLOW_UP: (
        "你现在要跟进用户之前提到过的一件事。"
        "自然地问一下进度，不要像老板催工。"
    ),
    StrategyType.GREET: (
        "你现在要和用户打招呼（早安或晚安）。"
        "简短、温暖、符合你的性格。"
        "可以加一句关于今天的话。"
    ),
    StrategyType.CHAT: (
        "你现在要主动找用户聊天。"
        "找一个自然的话题（基于用户最近在做的事、兴趣、或当前时间）。"
        "不要突兀，像朋友随口说的一句话。"
    ),
}


class StrategySelector:
    """
    策略选择器：根据触发类型和上下文，选出最合适的策略。

    选择逻辑：
    1. 从 TRIGGER_STRATEGY_MAP 获取候选策略列表
    2. 默认取第一个（最优先的）
    3. 未来 Phase 2：结合 Soul 性格偏好和用户反馈历史微调
    """

    def __init__(self, soul_content: str = ""):
        self.soul_content = soul_content
        # 未来：从 soul_content 解析性格类型，影响策略偏好

    def select(self, trigger: Trigger, context: dict) -> str:
        """
        选择策略类型。

        返回 StrategyType 常量字符串。
        """
        candidates = TRIGGER_STRATEGY_MAP.get(trigger.type, [StrategyType.CHAT])

        if not candidates:
            return StrategyType.CHAT

        # Phase 1: 简单取第一个（优先级最高的）
        # Phase 2: 根据 Soul 性格和用户偏好权重排序
        selected = candidates[0]

        logger.debug(
            f"Strategy selected: {selected} "
            f"(trigger={trigger.type}, candidates={candidates})"
        )
        return selected

    def get_instruction(self, strategy_type: str) -> str:
        """获取策略对应的 LLM 指令"""
        return STRATEGY_INSTRUCTIONS.get(strategy_type, "你现在要主动和用户说话。")
