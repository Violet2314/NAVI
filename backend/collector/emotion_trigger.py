"""
EmotionTrigger - 情绪趋势分析 + 主动对话触发

设计文档：docs/camera-emotion-design.md

职责：
  1. 接收 CameraCapture 的情绪回调，维护滑动窗口
  2. 检测持续负面情绪 → 生成 EMOTION_SHIFT 触发事件
  3. 检测情绪恢复 → 生成 EMOTION_RECOVERY 事件
  4. 提供情绪摘要（供 ContextBuilder 组装 LLM 上下文）
  5. 写入 emotion_records 时间序列表
"""
import logging
import sqlite3
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("navi.collector.emotion")

# 负面情绪集合
NEGATIVE_EMOTIONS = {"sad", "angry", "fear"}


class EmotionTrigger:
    """
    情绪趋势分析器

    滑动窗口分析最近 N 条情绪记录：
    - 持续负面 → EMOTION_SHIFT 触发
    - 从负面恢复 → EMOTION_RECOVERY 触发
    - 提供摘要供 LLM 上下文使用
    """

    def __init__(
        self,
        db_path: str,
        window_size: int = 20,
        negative_ratio_threshold: float = 0.6,
        min_records_for_trigger: int = 5,
    ):
        """
        Args:
            db_path: SQLite 数据库路径
            window_size: 滑动窗口大小
            negative_ratio_threshold: 负面情绪占比阈值（超过则触发）
            min_records_for_trigger: 最少需要多少条记录才做判断
        """
        self._db_path = db_path
        self._window_size = window_size
        self._negative_ratio_threshold = negative_ratio_threshold
        self._min_records = min_records_for_trigger

        # 滑动窗口：存储最近 N 条情绪记录
        self._emotion_window: deque = deque(maxlen=window_size)

        # 状态追踪
        self._was_negative_dominant = False  # 上次是否处于负面主导状态
        self._last_trigger_time: Optional[datetime] = None
        self._trigger_cooldown_sec = 900  # 触发冷却：15 分钟

    def on_emotion(self, emotion_data: dict) -> Optional[dict]:
        """
        接收一条情绪数据，分析趋势，返回触发事件（或 None）

        Args:
            emotion_data: {
                "emotion": str,
                "emotion_confidence": float,
                "user_present": bool,
                "captured_at": str,
            }

        Returns:
            触发事件 dict（供 ProactiveEngine 使用）或 None
            {
                "trigger_type": "emotion_shift" | "emotion_recovery",
                "context": { ... },
            }
        """
        emotion = emotion_data.get("emotion")
        if not emotion:
            return None

        confidence = emotion_data.get("emotion_confidence", 0.0)

        # 低置信度的情绪不计入分析
        if confidence < 0.4:
            return None

        # 加入窗口
        self._emotion_window.append({
            "emotion": emotion,
            "confidence": confidence,
            "time": datetime.now(),
        })

        # 记录数不够，不做判断
        if len(self._emotion_window) < self._min_records:
            return None

        # 计算负面情绪占比
        negative_count = sum(
            1 for e in self._emotion_window if e["emotion"] in NEGATIVE_EMOTIONS
        )
        ratio = negative_count / len(self._emotion_window)
        is_negative_dominant = ratio >= self._negative_ratio_threshold

        trigger = None

        # 检查冷却
        now = datetime.now()
        in_cooldown = (
            self._last_trigger_time is not None
            and (now - self._last_trigger_time).total_seconds() < self._trigger_cooldown_sec
        )

        if is_negative_dominant and not self._was_negative_dominant and not in_cooldown:
            # 从正常 → 负面主导：触发 EMOTION_SHIFT
            dominant_negative = self._get_dominant_emotion(negative_only=True)
            trigger = {
                "trigger_type": "emotion_shift",
                "context": {
                    "negative_ratio": round(ratio, 2),
                    "dominant_emotion": dominant_negative,
                    "window_size": len(self._emotion_window),
                    "recent_emotions": self._get_recent_labels(5),
                },
            }
            self._last_trigger_time = now
            logger.info(
                f"⚠️ 情绪触发: EMOTION_SHIFT "
                f"(负面占比 {ratio:.0%}, 主导情绪 {dominant_negative})"
            )

        elif not is_negative_dominant and self._was_negative_dominant and not in_cooldown:
            # 从负面 → 恢复：触发 EMOTION_RECOVERY
            current_emotion = emotion
            trigger = {
                "trigger_type": "emotion_recovery",
                "context": {
                    "recovered_to": current_emotion,
                    "previous_negative_ratio": round(ratio, 2),
                    "recent_emotions": self._get_recent_labels(5),
                },
            }
            self._last_trigger_time = now
            logger.info(f"✅ 情绪恢复: EMOTION_RECOVERY (恢复到 {current_emotion})")

        self._was_negative_dominant = is_negative_dominant
        return trigger

    def save_record(self, emotion_data: dict, window_activity_id: Optional[int] = None):
        """
        写入 emotion_records 时间序列表

        Args:
            emotion_data: CameraCapture 的输出 dict
            window_activity_id: 关联的当前活动段 ID（可选）
        """
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """INSERT INTO emotion_records 
                   (detected_at, user_present, emotion, confidence, face_count, window_activity_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    emotion_data.get("captured_at", datetime.now().isoformat()),
                    1 if emotion_data.get("user_present", False) else 0,
                    emotion_data.get("emotion"),
                    emotion_data.get("emotion_confidence"),
                    emotion_data.get("face_count", 0),
                    window_activity_id,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"写入 emotion_records 失败: {e}")

    def get_recent_summary(self, minutes: int = 30) -> dict:
        """
        返回最近情绪摘要（供 ContextBuilder 使用）

        Returns:
            {
                "dominant_emotion": "neutral",
                "emotion_distribution": {"neutral": 12, "happy": 3, "sad": 2},
                "trend": "stable" | "declining" | "improving",
                "negative_streak": 0,
                "total_records": 20,
            }
        """
        if not self._emotion_window:
            return {
                "dominant_emotion": None,
                "emotion_distribution": {},
                "trend": "unknown",
                "negative_streak": 0,
                "total_records": 0,
            }

        # 统计分布
        distribution: Dict[str, int] = {}
        for entry in self._emotion_window:
            e = entry["emotion"]
            distribution[e] = distribution.get(e, 0) + 1

        # 主导情绪
        dominant = max(distribution, key=distribution.get)

        # 负面连续计数（从最新往前数）
        negative_streak = 0
        for entry in reversed(self._emotion_window):
            if entry["emotion"] in NEGATIVE_EMOTIONS:
                negative_streak += 1
            else:
                break

        # 趋势分析（窗口前半 vs 后半）
        half = len(self._emotion_window) // 2
        if half > 0:
            first_half = list(self._emotion_window)[:half]
            second_half = list(self._emotion_window)[half:]

            first_neg = sum(1 for e in first_half if e["emotion"] in NEGATIVE_EMOTIONS)
            second_neg = sum(1 for e in second_half if e["emotion"] in NEGATIVE_EMOTIONS)

            first_ratio = first_neg / len(first_half)
            second_ratio = second_neg / len(second_half)

            if second_ratio > first_ratio + 0.2:
                trend = "declining"
            elif first_ratio > second_ratio + 0.2:
                trend = "improving"
            else:
                trend = "stable"
        else:
            trend = "unknown"

        return {
            "dominant_emotion": dominant,
            "emotion_distribution": distribution,
            "trend": trend,
            "negative_streak": negative_streak,
            "total_records": len(self._emotion_window),
        }

    def format_for_llm(self) -> str:
        """
        格式化情绪摘要为 LLM 可读的纯文字

        Returns:
            类似：
            "- 近 30 分钟情绪：专注(×4) → 烦躁(×2) → 专注(×3)
             - 情绪趋势：整体稳定"
        """
        if not self._emotion_window:
            return "- 情绪数据：暂无（摄像头未启用或刚启动）"

        summary = self.get_recent_summary()

        # 构建情绪序列描述
        labels = [e["emotion"] for e in self._emotion_window]
        # 压缩连续相同情绪
        compressed = []
        if labels:
            current = labels[0]
            count = 1
            for l in labels[1:]:
                if l == current:
                    count += 1
                else:
                    compressed.append(f"{current}(×{count})")
                    current = l
                    count = 1
            compressed.append(f"{current}(×{count})")

        sequence = " → ".join(compressed[-6:])  # 最多显示最近 6 段

        # 趋势中文映射
        trend_map = {
            "stable": "整体稳定",
            "declining": "趋向低落",
            "improving": "逐渐好转",
            "unknown": "数据不足",
        }
        trend_text = trend_map.get(summary["trend"], "未知")

        lines = [
            f"- 近期情绪：{sequence}",
            f"- 情绪趋势：{trend_text}",
        ]

        if summary["negative_streak"] >= 3:
            lines.append(f"- ⚠️ 连续 {summary['negative_streak']} 次检测到负面情绪")

        return "\n".join(lines)

    # ── 内部方法 ──

    def _get_dominant_emotion(self, negative_only: bool = False) -> str:
        """获取窗口内主导情绪"""
        counts: Dict[str, int] = {}
        for entry in self._emotion_window:
            e = entry["emotion"]
            if negative_only and e not in NEGATIVE_EMOTIONS:
                continue
            counts[e] = counts.get(e, 0) + 1
        return max(counts, key=counts.get) if counts else "neutral"

    def _get_recent_labels(self, n: int = 5) -> List[str]:
        """获取最近 N 条情绪标签"""
        return [e["emotion"] for e in list(self._emotion_window)[-n:]]
