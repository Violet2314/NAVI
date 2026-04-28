"""
experience.py — 经验学习：反馈三态 + 抑制机制

参考：
- PRIME 论文的三区记忆进化（成功/失败/偏好）
- thunlp/ProactiveAgent 的反馈三态（accept/reject/ignore）

关键规则（来自 ProactiveAgent prompt）：
  "Pay attention to the user's feedback on your assistance:
   Try not to disturb when they IGNORE,
   try another approach when they REJECT,
   even if they ACCEPT, don't propose related tasks next turn."
"""
import logging
from datetime import datetime
from typing import Optional

from proactive.gate import GateKeeper
from proactive.constants import (
    FEEDBACK_IGNORE_TIMEOUT_SEC,
    FEEDBACK_ACCEPT_DELTA,
    FEEDBACK_REJECT_DELTA,
    FEEDBACK_IGNORE_DELTA,
    FEEDBACK_REJECT_SILENT_HOURS,
    REJECT_KEYWORDS,
)

logger = logging.getLogger("navi.proactive.experience")


class FeedbackType:
    """反馈三态"""
    ACCEPT = "accept"    # 用户积极回应（回复了 / 说谢谢）
    REJECT = "reject"    # 用户明确拒绝（"别烦我" / "不需要"）
    IGNORE = "ignore"    # 用户无视（超过 N 分钟未回复）


class ExperienceLearner:
    """
    从用户对主动消息的反应中学习。

    三态处理策略：
    - ACCEPT → 记录成功 + 微调阈值 -0.05 + 抑制下一轮类似话题
    - REJECT → 静默模式 + 微调阈值 +0.10 + 记录偏好
    - IGNORE → 温和降权 +0.05
    """

    def __init__(self, gate_keeper: GateKeeper):
        self.gate_keeper = gate_keeper
        # 话题抑制列表：最近 ACCEPT 过的 trigger_type
        self._recent_accepted_topics: list[str] = []
        # 主动消息记录：event_id → (trigger_type, strategy, timestamp)
        self._pending_feedback: dict[str, dict] = {}

    # ── 注册主动消息（等待反馈）──

    def register_proactive_event(
        self,
        event_id: str,
        trigger_type: str,
        strategy: str,
    ) -> None:
        """注册一条刚发出的主动消息，开始等待反馈"""
        self._pending_feedback[event_id] = {
            "trigger_type": trigger_type,
            "strategy": strategy,
            "sent_at": datetime.now(),
        }

    # ── 处理反馈 ──

    def process_feedback(
        self,
        event_id: str,
        feedback: str,
    ) -> None:
        """
        处理用户对主动消息的反馈。

        feedback: FeedbackType.ACCEPT / REJECT / IGNORE
        """
        info = self._pending_feedback.pop(event_id, None)
        if not info:
            logger.debug(f"No pending event for {event_id}, skip")
            return

        trigger_type = info["trigger_type"]
        strategy = info["strategy"]

        if feedback == FeedbackType.ACCEPT:
            self._on_accept(trigger_type, strategy)
        elif feedback == FeedbackType.REJECT:
            self._on_reject(trigger_type, strategy)
        elif feedback == FeedbackType.IGNORE:
            self._on_ignore(trigger_type, strategy)

        logger.info(
            f"Feedback recorded: {feedback} for {trigger_type}/{strategy} "
            f"(event={event_id})"
        )

    def _on_accept(self, trigger_type: str, strategy: str):
        """接受：降低阈值 + 抑制下一轮"""
        self.gate_keeper.adjust_threshold(trigger_type, delta=FEEDBACK_ACCEPT_DELTA)
        self._recent_accepted_topics.append(trigger_type)

    def _on_reject(self, trigger_type: str, strategy: str):
        """拒绝：静默 + 提高阈值"""
        self.gate_keeper.enter_silent_mode(duration_hours=FEEDBACK_REJECT_SILENT_HOURS)
        self.gate_keeper.adjust_threshold(trigger_type, delta=FEEDBACK_REJECT_DELTA)

    def _on_ignore(self, trigger_type: str, strategy: str):
        """无视：温和降权"""
        self.gate_keeper.adjust_threshold(trigger_type, delta=FEEDBACK_IGNORE_DELTA)

    # ── 话题抑制 ──

    def is_topic_suppressed(self, trigger_type: str) -> bool:
        """检查是否因为上轮 ACCEPT 而需要抑制"""
        return trigger_type in self._recent_accepted_topics

    def clear_suppression(self) -> None:
        """新的巡检周期开始时清除抑制列表"""
        if self._recent_accepted_topics:
            logger.debug(
                f"Clearing topic suppression: {self._recent_accepted_topics}"
            )
        self._recent_accepted_topics.clear()

    # ── 超时检查 ──

    def check_timeouts(self) -> list[str]:
        """
        检查是否有主动消息超时未收到反馈 → 判定为 IGNORE。
        返回被判定为 IGNORE 的 event_id 列表。
        """
        now = datetime.now()
        timed_out = []

        for event_id, info in list(self._pending_feedback.items()):
            elapsed = (now - info["sent_at"]).total_seconds()
            if elapsed >= FEEDBACK_IGNORE_TIMEOUT_SEC:
                timed_out.append(event_id)
                self.process_feedback(event_id, FeedbackType.IGNORE)

        return timed_out

    # ── 用户消息分析 ──

    @staticmethod
    def classify_user_response(text: str) -> str:
        """
        分析用户回复，判断是 ACCEPT 还是 REJECT。

        简单规则：
        - 包含拒绝关键词 → REJECT
        - 其他 → ACCEPT（有回复就算接受）
        """
        text_lower = text.lower().strip()
        for keyword in REJECT_KEYWORDS:
            if keyword in text_lower:
                return FeedbackType.REJECT
        return FeedbackType.ACCEPT

    def get_pending_count(self) -> int:
        """获取等待反馈的主动消息数量"""
        return len(self._pending_feedback)
