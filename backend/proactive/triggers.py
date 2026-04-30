"""
triggers.py — 触发器：检测"该主动的时刻"

定义所有可能触发主动行为的条件。
分为 System 1（实时事件驱动）和 System 2（定时巡检深度分析）两类。

v2: TRIGGER_CONFIG 改为函数调用，确保运行时修改 constants 后能实时生效。
"""
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from proactive import constants as C

logger = logging.getLogger("navi.proactive.triggers")


# ── 数据模型 ──────────────────────────────────────────────────────────────

@dataclass
class Trigger:
    """一个主动行为触发事件"""
    type: str               # 触发类型（见 TriggerType 常量）
    urgency: float          # 紧急度 0-1
    context: dict = field(default_factory=dict)   # 触发时的上下文快照
    timestamp: datetime = field(default_factory=datetime.now)


class TriggerType:
    """触发类型常量"""
    # System 1: 实时触发（由 Collector 回调驱动）
    LONG_WORK    = "long_work"        # 连续工作超过 N 小时
    SLACKING     = "slacking"         # 切换到娱乐应用超过 N 分钟
    BLACKLIST    = "blacklist"        # 打开黑名单应用
    IDLE         = "idle"             # 长时间无活动

    # System 1: 情绪触发（由 CameraCapture + EmotionTrigger 驱动）
    EMOTION_SHIFT    = "emotion_shift"      # 持续负面情绪
    EMOTION_RECOVERY = "emotion_recovery"   # 情绪恢复

    # System 2: 慢速推理触发（定时巡检）
    LATE_NIGHT   = "late_night"       # 深夜仍在使用电脑
    MORNING_GREET = "morning_greet"   # 每天第一次检测到活动
    EVENING_GREET = "evening_greet"   # 傍晚/下班时间
    TASK_FOLLOW_UP = "task_follow_up" # 用户提过的待办到期
    PERIODIC_CHAT = "periodic_chat"   # 长时间无互动


def get_trigger_config() -> dict:
    """
    每次调用实时从 constants 模块读取最新值。
    确保前端/API 修改 constants 属性后，触发检测立即生效。
    """
    return {
        TriggerType.LONG_WORK: {
            "threshold_minutes": C.LONG_WORK_THRESHOLD_MIN,
            "urgency": C.LONG_WORK_URGENCY,
        },
        TriggerType.SLACKING: {
            "threshold_minutes": C.SLACKING_THRESHOLD_MIN,
            "urgency": C.SLACKING_URGENCY,
        },
        TriggerType.BLACKLIST: {
            "urgency": C.BLACKLIST_URGENCY,
        },
        TriggerType.IDLE: {
            "threshold_minutes": C.IDLE_THRESHOLD_MIN,
            "urgency": C.IDLE_URGENCY,
        },
        TriggerType.LATE_NIGHT: {
            "start_hour": C.LATE_NIGHT_START_HOUR,
            "urgency": C.LATE_NIGHT_URGENCY,
        },
        TriggerType.MORNING_GREET: {
            "urgency": C.MORNING_GREET_URGENCY,
        },
        TriggerType.EVENING_GREET: {
            "start_hour": C.EVENING_GREET_START_HOUR,
            "end_hour": C.EVENING_GREET_END_HOUR,
            "urgency": C.EVENING_GREET_URGENCY,
        },
        TriggerType.PERIODIC_CHAT: {
            "threshold_hours": C.PERIODIC_CHAT_THRESHOLD_HOURS,
            "urgency": C.PERIODIC_CHAT_URGENCY,
        },
        TriggerType.EMOTION_SHIFT: {
            "urgency": C.EMOTION_SHIFT_URGENCY,
        },
        TriggerType.EMOTION_RECOVERY: {
            "urgency": C.EMOTION_RECOVERY_URGENCY,
        },
    }


# ── 触发检测器 ─────────────────────────────────────────────────────────────

class TriggerDetector:
    """
    触发检测器：从 Collector 事件和 DB 上下文中检测触发条件。

    两个入口：
    - detect_realtime(event_type, data) — System 1 调用，处理即时事件
    - detect_deep(context) — System 2 调用，做深度分析
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._today_greeted_morning = False
        self._today_greeted_evening = False
        self._last_reset_date: Optional[str] = None

    def _reset_daily_flags(self):
        """每天重置日级标志"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_reset_date != today:
            self._today_greeted_morning = False
            self._today_greeted_evening = False
            self._last_reset_date = today

    # ── System 1：实时事件触发检测 ──

    def detect_realtime(self, event_type: str, data: dict) -> list[Trigger]:
        """
        处理 Collector 回调事件，返回零或多个触发。

        event_type:
          - "activity_change" → data 包含 segment_data（活动段切换）
          - "blacklist_hit"   → data 包含 app_info
          - "idle_change"     → data 包含 is_idle, seconds
        """
        triggers = []
        TC = get_trigger_config()

        if event_type == "activity_change":
            triggers.extend(self._check_activity_change(data))

        elif event_type == "blacklist_hit":
            cfg = TC[TriggerType.BLACKLIST]
            triggers.append(Trigger(
                type=TriggerType.BLACKLIST,
                urgency=cfg["urgency"],
                context={"app": data.get("process_name", "unknown")},
            ))

        elif event_type == "idle_change":
            if data.get("is_idle"):
                cfg = TC[TriggerType.IDLE]
                idle_min = data.get("seconds", 0) / 60
                if idle_min >= cfg["threshold_minutes"]:
                    triggers.append(Trigger(
                        type=TriggerType.IDLE,
                        urgency=cfg["urgency"],
                        context={"idle_minutes": round(idle_min, 1)},
                    ))

        elif event_type == "emotion_shift":
            trigger_type = data.get("trigger_type", "emotion_shift")
            if trigger_type == "emotion_recovery":
                cfg = TC[TriggerType.EMOTION_RECOVERY]
                triggers.append(Trigger(
                    type=TriggerType.EMOTION_RECOVERY,
                    urgency=cfg["urgency"],
                    context=data.get("context", {}),
                ))
            else:
                cfg = TC[TriggerType.EMOTION_SHIFT]
                triggers.append(Trigger(
                    type=TriggerType.EMOTION_SHIFT,
                    urgency=cfg["urgency"],
                    context=data.get("context", {}),
                ))

        return triggers

    def _check_activity_change(self, segment_data: dict) -> list[Trigger]:
        """
        检查活动段切换是否触发摸鱼/长时间工作。
        v3: 基于 category_breakdown 分类占比判断，而非单一分类。
        """
        triggers = []
        TC = get_trigger_config()
        duration = segment_data.get("duration_sec", 0)
        breakdown = segment_data.get("category_breakdown", {})
        category = segment_data.get("app_category", segment_data.get("category", ""))

        # 计算娱乐占比（来自 breakdown 或回退到单一分类）
        ent_sec = breakdown.get("entertainment", 0) if breakdown else (
            duration if category == "entertainment" else 0
        )
        total_sec = sum(breakdown.values()) if breakdown else duration

        if total_sec > 0:
            ent_ratio = ent_sec / total_sec

            # 娱乐占比 ≥ 40% 且娱乐时长超阈值 → 触发摸鱼提醒
            cfg = TC[TriggerType.SLACKING]
            if ent_ratio >= 0.4 and ent_sec / 60 >= cfg["threshold_minutes"]:
                # 构建更丰富的上下文
                breakdown_desc = ", ".join(
                    f"{k}: {v//60}m{v%60}s" for k, v in
                    sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
                ) if breakdown else f"entertainment: {ent_sec}s"

                triggers.append(Trigger(
                    type=TriggerType.SLACKING,
                    urgency=cfg["urgency"],
                    context={
                        "app": segment_data.get("process_name", "unknown"),
                        "title": segment_data.get("window_title", ""),
                        "duration_min": round(duration / 60, 1),
                        "entertainment_min": round(ent_sec / 60, 1),
                        "entertainment_ratio": round(ent_ratio * 100),
                        "breakdown": breakdown_desc,
                    },
                ))

        return triggers

    # ── System 2：定时巡检深度触发检测 ──

    def detect_deep(self, context: dict) -> list[Trigger]:
        """
        从 DB 查询结果（context）中做深度分析。

        context 结构：
          {
            "recent_activities": [(process, title, category, duration, started_at), ...],
            "current_time": "ISO datetime string",
          }
        """
        self._reset_daily_flags()
        triggers = []
        now = datetime.now()
        activities = context.get("recent_activities", [])

        # 1. 深夜关怀
        triggers.extend(self._check_late_night(now, activities))

        # 2. 早安问候
        triggers.extend(self._check_morning_greet(now, activities))

        # 3. 晚间问候
        triggers.extend(self._check_evening_greet(now))

        # 4. 连续工作检测
        triggers.extend(self._check_long_work(activities))

        # 5. 长时间无互动
        triggers.extend(self._check_periodic_chat())

        return triggers

    def _check_late_night(self, now: datetime, activities: list) -> list[Trigger]:
        """23:00 后仍有活动 → 深夜关怀"""
        cfg = get_trigger_config()[TriggerType.LATE_NIGHT]
        if now.hour >= cfg["start_hour"] and activities:
            # 最近 10 分钟有活动才触发
            for act in activities[:3]:
                started = act[4] if len(act) > 4 else ""
                if started:
                    try:
                        act_time = datetime.fromisoformat(started)
                        if (now - act_time).total_seconds() < C.LATE_NIGHT_RECENT_SEC:
                            return [Trigger(
                                type=TriggerType.LATE_NIGHT,
                                urgency=cfg["urgency"],
                                context={"hour": now.hour, "minute": now.minute},
                            )]
                    except (ValueError, TypeError):
                        pass
        return []

    def _check_morning_greet(self, now: datetime, activities: list) -> list[Trigger]:
        """早上第一次检测到活动 → 早安"""
        if self._today_greeted_morning:
            return []
        cfg = get_trigger_config()[TriggerType.MORNING_GREET]
        if C.MORNING_GREET_START_HOUR <= now.hour <= C.MORNING_GREET_END_HOUR and activities:
            self._today_greeted_morning = True
            return [Trigger(
                type=TriggerType.MORNING_GREET,
                urgency=cfg["urgency"],
                context={"hour": now.hour},
            )]
        return []

    def _check_evening_greet(self, now: datetime) -> list[Trigger]:
        """傍晚问候"""
        if self._today_greeted_evening:
            return []
        cfg = get_trigger_config()[TriggerType.EVENING_GREET]
        if cfg["start_hour"] <= now.hour <= cfg["end_hour"]:
            self._today_greeted_evening = True
            return [Trigger(
                type=TriggerType.EVENING_GREET,
                urgency=cfg["urgency"],
                context={"hour": now.hour},
            )]
        return []

    def _check_long_work(self, activities: list) -> list[Trigger]:
        """
        连续工作超过阈值 → 提醒休息。
        
        v2: 容忍短暂中断（≤10分钟的非工作活动不算打断）。
        例如：写代码2小时 → 看5分钟微信 → 继续写代码 → 仍然算连续工作。
        但如果中间插了30分钟娱乐，就重新计算。
        """
        cfg = get_trigger_config()[TriggerType.LONG_WORK]
        threshold_sec = cfg["threshold_minutes"] * 60
        # 允许的最大中断时长（秒）：短于此的非工作活动不打断连续工作计时
        MAX_BREAK_SEC = 600  # 10 分钟

        total_work_sec = 0
        accumulated_break_sec = 0  # 当前连续中断累积

        for act in activities:
            category = act[2] if len(act) > 2 else ""
            duration = act[3] if len(act) > 3 else 0

            if category in ("work", "learning"):
                # 工作类活动：累加工作时长，重置中断累积
                total_work_sec += duration
                accumulated_break_sec = 0
            else:
                # 非工作类活动：累积中断时间
                accumulated_break_sec += duration
                if accumulated_break_sec > MAX_BREAK_SEC:
                    # 中断太久，停止往回算
                    break

        if total_work_sec >= threshold_sec:
            return [Trigger(
                type=TriggerType.LONG_WORK,
                urgency=cfg["urgency"],
                context={"work_minutes": round(total_work_sec / 60, 1)},
            )]
        return []

    def _check_periodic_chat(self) -> list[Trigger]:
        """长时间无互动 → 闲聊"""
        cfg = get_trigger_config()[TriggerType.PERIODIC_CHAT]
        threshold_sec = cfg["threshold_hours"] * 3600

        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute("""
                SELECT MAX(created_at) FROM chat_messages
                WHERE role IN ('user', 'assistant')
            """).fetchone()
            conn.close()

            if row and row[0]:
                last_chat = datetime.fromisoformat(row[0])
                elapsed = (datetime.now() - last_chat).total_seconds()
                if elapsed >= threshold_sec:
                    return [Trigger(
                        type=TriggerType.PERIODIC_CHAT,
                        urgency=cfg["urgency"],
                        context={"hours_since_chat": round(elapsed / 3600, 1)},
                    )]
        except Exception as e:
            logger.debug(f"periodic_chat check failed: {e}")

        return []