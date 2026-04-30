"""
thought_buffer.py — 想法缓冲区：合并窗口，防连续轰炸

参考 xybruceliu/thoughtful-agents 的 thought_reservoir 设计。

核心问题：
- 用户连续切换 3 个应用 → 可能产生 3 个触发，但只应该说 1 句综合性的话
- 深夜 + 长时间工作 同时触发 → 应该合并为一句话而不是两句

工作方式：
1. System 1/2 检测到触发 → add() 放入缓冲区
2. 合并窗口（默认 60s）内的触发被累积
3. 窗口到期后 flush() → 取紧急度最高的，合并上下文
4. 只输出一个最终的 merged trigger 送到后续流程
"""
import logging
from datetime import datetime
from typing import Optional

from proactive.triggers import Trigger
from proactive.constants import BUFFER_MERGE_WINDOW_SEC

logger = logging.getLogger("navi.proactive.buffer")


class ThoughtBuffer:
    """
    想法缓冲区：收集触发，延迟合并，避免连续多条消息。
    线程安全不是问题 — 仅在 async 上下文中使用。
    """

    def __init__(self, merge_window_sec: int = BUFFER_MERGE_WINDOW_SEC):
        self.merge_window = merge_window_sec
        self._pending: list[Trigger] = []
        self._first_add_time: Optional[datetime] = None

    @property
    def size(self) -> int:
        return len(self._pending)

    @property
    def is_empty(self) -> bool:
        return len(self._pending) == 0

    def add(self, trigger: Trigger) -> None:
        """将触发放入缓冲区，开始/延续合并窗口"""
        if not self._first_add_time:
            self._first_add_time = datetime.now()
        self._pending.append(trigger)
        logger.info(
            f"📥 Buffer add: {trigger.type} (urgency={trigger.urgency:.2f}), "
            f"buffer_size={len(self._pending)}, "
            f"will_flush_in={self.merge_window}s"
        )

    def is_ready(self) -> bool:
        """合并窗口是否到期（到期了才允许 flush）"""
        if not self._first_add_time or not self._pending:
            return False
        elapsed = (datetime.now() - self._first_add_time).total_seconds()
        return elapsed >= self.merge_window

    def flush(self) -> Optional[Trigger]:
        """
        合并窗口到期后，输出一个最终触发。

        合并策略：
        - 取紧急度最高的 trigger 作为主触发
        - 将其他 trigger 的类型和紧急度合并到 context["also_triggered"]
        - 清空缓冲区，重置窗口

        返回 None 如果缓冲区为空。
        """
        if not self._pending:
            return None

        # 按紧急度降序排列
        sorted_triggers = sorted(
            self._pending,
            key=lambda t: t.urgency,
            reverse=True,
        )
        primary = sorted_triggers[0]

        # 合并其他触发的上下文
        if len(sorted_triggers) > 1:
            primary.context["also_triggered"] = [
                {"type": t.type, "urgency": t.urgency}
                for t in sorted_triggers[1:]
            ]
            logger.info(
                f"Buffer merged {len(sorted_triggers)} triggers → "
                f"primary={primary.type} (urgency={primary.urgency:.2f})"
            )

        # 清空
        self._pending.clear()
        self._first_add_time = None
        return primary

    def force_flush(self) -> Optional[Trigger]:
        """强制立刻 flush，不等合并窗口（用于高紧急度场景）"""
        return self.flush()

    def clear(self) -> None:
        """丢弃所有缓冲（引擎关闭时调用）"""
        self._pending.clear()
        self._first_add_time = None
