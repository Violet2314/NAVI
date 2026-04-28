"""
gate.py — 准入门控：决定"该不该说"

Phase 1：纯规则，不调 LLM，零 token 消耗
Phase 2（未来）：追加自适应维度，从 FactMemory 学习

设计原则（来自论文审查）：
- ProactiveAgent 用 LLM 一次判断该不该说 — 太贵，不适合常驻后台
- thoughtful-agents 用一个 intrinsic_motivation 分数 — 适中
- BAO 论文：过度主动比被动更糟糕
- v2 决策：先硬性规则零成本，后期从经验中学习
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from proactive.triggers import Trigger
from proactive.constants import (
    GATE_MIN_INTERVAL_MIN, GATE_MAX_DAILY_PROACTIVE,
    GATE_QUIET_HOURS_START, GATE_QUIET_HOURS_END,
    GATE_BASE_THRESHOLD,
    GATE_THRESHOLD_OFFSET_MIN, GATE_THRESHOLD_OFFSET_MAX,
    SILENT_MODE_DEFAULT_HOURS,
    STRATEGY_COOLDOWN_GREET_MAX_DAILY,
    STRATEGY_COOLDOWN_REMIND_INTERVAL_MIN,
    STRATEGY_COOLDOWN_TEASE_INTERVAL_MIN,
    STRATEGY_COOLDOWN_CHAT_MAX_DAILY,
    STRATEGY_COOLDOWN_CARE_INTERVAL_MIN,
    STRATEGY_COOLDOWN_SHARE_INTERVAL_MIN,
    STRATEGY_COOLDOWN_FOLLOW_UP_MAX_DAILY,
    STRATEGY_COOLDOWN_SUMMARIZE_MAX_DAILY,
)

logger = logging.getLogger("navi.proactive.gate")


# ── 策略级冷却配置（从 constants.py 统一读取）──────────────────────────

STRATEGY_COOLDOWN = {
    "greet":     {"max_daily": STRATEGY_COOLDOWN_GREET_MAX_DAILY},
    "remind":    {"min_interval_minutes": STRATEGY_COOLDOWN_REMIND_INTERVAL_MIN},
    "tease":     {"min_interval_minutes": STRATEGY_COOLDOWN_TEASE_INTERVAL_MIN},
    "chat":      {"max_daily": STRATEGY_COOLDOWN_CHAT_MAX_DAILY},
    "care":      {"min_interval_minutes": STRATEGY_COOLDOWN_CARE_INTERVAL_MIN},
    "share":     {"min_interval_minutes": STRATEGY_COOLDOWN_SHARE_INTERVAL_MIN},
    "follow_up": {"max_daily": STRATEGY_COOLDOWN_FOLLOW_UP_MAX_DAILY},
    "summarize": {"max_daily": STRATEGY_COOLDOWN_SUMMARIZE_MAX_DAILY},
}


class GateKeeper:
    """
    准入门控：决定"该不该说"。

    逐条检查硬性规则，任何一条不过就拒绝。
    返回 (是否通过, 拒绝原因) 元组。
    """

    def __init__(
        self,
        db_path: str,
        min_interval_minutes: int = GATE_MIN_INTERVAL_MIN,
        max_daily_proactive: int = GATE_MAX_DAILY_PROACTIVE,
        quiet_hours: tuple[int, int] = (GATE_QUIET_HOURS_START, GATE_QUIET_HOURS_END),
        base_threshold: float = GATE_BASE_THRESHOLD,
    ):
        self.db_path = db_path
        self.min_interval_minutes = min_interval_minutes
        self.max_daily_proactive = max_daily_proactive
        self.quiet_hours = quiet_hours
        self.base_threshold = base_threshold

        # 运行时状态
        self._silent_until: Optional[datetime] = None
        self._proactive_log: list[dict] = []   # 今日主动发言记录
        self._last_reset_date: Optional[str] = None
        # Phase 2: 每个 trigger_type 的阈值偏移（从经验学习）
        self._threshold_offsets: dict[str, float] = {}

    # ── 核心判断 ──

    def should_speak(self, trigger: Trigger) -> tuple[bool, str]:
        """
        返回 (是否通过, 拒绝/通过原因)。
        逐条检查，任何一条不过就拒绝。
        """
        self._reset_daily_log()
        now = datetime.now()

        # 1. 静默模式：用户主动要求安静
        if self._silent_until and now < self._silent_until:
            remaining = int((self._silent_until - now).total_seconds() / 60)
            return False, f"silent_mode ({remaining}min remaining)"

        # 2. 静默时段
        if self.quiet_hours[0] <= now.hour < self.quiet_hours[1]:
            return False, f"quiet_hours ({self.quiet_hours[0]}:00-{self.quiet_hours[1]}:00)"

        # 3. 全局冷却
        last_time = self._get_last_proactive_time()
        if last_time:
            elapsed_sec = (now - last_time).total_seconds()
            if elapsed_sec < self.min_interval_minutes * 60:
                return False, (
                    f"global_cooldown ({int(elapsed_sec)}s < "
                    f"{self.min_interval_minutes * 60}s)"
                )

        # 4. 每日上限
        today_count = len(self._proactive_log)
        if today_count >= self.max_daily_proactive:
            return False, f"daily_limit ({today_count} >= {self.max_daily_proactive})"

        # 5. 紧急度阈值
        adjusted = self._get_adjusted_threshold(trigger.type)
        if trigger.urgency < adjusted:
            return False, f"urgency ({trigger.urgency:.2f} < {adjusted:.2f})"

        return True, "passed"

    # ── 静默模式 ──

    def enter_silent_mode(self, duration_hours: float = SILENT_MODE_DEFAULT_HOURS) -> None:
        """用户说"别烦我"时调用"""
        self._silent_until = datetime.now() + timedelta(hours=duration_hours)
        logger.info(f"Entering silent mode for {duration_hours}h")

    def exit_silent_mode(self) -> None:
        """手动退出静默模式"""
        self._silent_until = None
        logger.info("Exiting silent mode")

    @property
    def is_silent(self) -> bool:
        if not self._silent_until:
            return False
        return datetime.now() < self._silent_until

    # ── 记录主动发言 ──

    def record_proactive(self, trigger_type: str, strategy_type: str) -> None:
        """每次成功主动发言后调用"""
        self._proactive_log.append({
            "trigger_type": trigger_type,
            "strategy_type": strategy_type,
            "time": datetime.now(),
        })

    # ── Phase 2：阈值调整 ──

    def adjust_threshold(self, trigger_type: str, delta: float) -> None:
        """
        微调某个 trigger_type 的阈值偏移。
        delta > 0 → 更难触发（用户不喜欢）
        delta < 0 → 更容易触发（用户喜欢）
        """
        current = self._threshold_offsets.get(trigger_type, 0.0)
        new_val = max(GATE_THRESHOLD_OFFSET_MIN, min(GATE_THRESHOLD_OFFSET_MAX, current + delta))
        self._threshold_offsets[trigger_type] = new_val
        logger.info(
            f"Threshold adjusted: {trigger_type} offset "
            f"{current:.2f} → {new_val:.2f}"
        )

    # ── 内部方法 ──

    def _get_adjusted_threshold(self, trigger_type: str) -> float:
        """获取某个 trigger_type 的实际阈值（base + offset）"""
        offset = self._threshold_offsets.get(trigger_type, 0.0)
        return max(0.1, min(0.9, self.base_threshold + offset))  # 绝对安全边界

    def _get_last_proactive_time(self) -> Optional[datetime]:
        """获取最后一次主动发言时间"""
        if not self._proactive_log:
            return None
        return self._proactive_log[-1]["time"]

    def _reset_daily_log(self) -> None:
        """每天重置日志"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_reset_date != today:
            self._proactive_log.clear()
            self._last_reset_date = today

    def get_stats(self) -> dict:
        """获取当前门控状态（用于调试/前端显示）"""
        return {
            "is_silent": self.is_silent,
            "silent_until": self._silent_until.isoformat() if self._silent_until else None,
            "today_count": len(self._proactive_log),
            "max_daily": self.max_daily_proactive,
            "threshold_offsets": dict(self._threshold_offsets),
        }
