"""
engine.py — 主动对话引擎：后台循环，串联所有模块

双系统架构（参考 ProAct 论文）：
- System 1: Collector 回调 → 实时触发检测（同步→async 桥接）
- System 2: 定时巡检 → 查 DB 深度分析

数据流：
  Collector 回调 / DB 查询 → TriggerDetector → ThoughtBuffer
  → GateKeeper → StrategySelector → LLM Generator → Dispatcher

集成方式：
- System 1 通过 on_activity_change 等回调接入（同步→async 桥接）
- System 2 通过 sqlite3 查询 window_activities 表接入
- 不依赖 MessageBus 的 topic 订阅（MessageBus 无此能力）
"""
import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from proactive.triggers import Trigger, TriggerDetector
from proactive.thought_buffer import ThoughtBuffer
from proactive.gate import GateKeeper
from proactive.strategies import StrategySelector
from proactive.generator import ProactiveMessageGenerator
from proactive.dispatcher import Dispatcher
from proactive.experience import ExperienceLearner
from proactive.constants import (
    PATROL_INTERVAL_SEC, PATROL_INITIAL_DELAY_SEC,
    EVENT_CONSUME_TIMEOUT_SEC,
    BUFFER_MERGE_WINDOW_SEC, BUFFER_CHECK_INTERVAL_SEC,
    FEEDBACK_CHECK_INTERVAL_SEC,
    DB_LOOKBACK_HOURS, DB_LOOKBACK_LIMIT,
)

if TYPE_CHECKING:
    from channels.local_ws import LocalWSChannel
    from session.manager import SessionManager

logger = logging.getLogger("navi.proactive.engine")


class ProactiveEngine:
    """
    主动对话引擎 — Navi 的"后台大脑"。

    启动后在后台持续运行三个 async 任务：
    1. _system1_consumer: 消费 Collector 回调事件
    2. _system2_patrol: 定时巡检 DB
    3. _buffer_flusher: 定期检查 ThoughtBuffer 并处理

    使用方式：
        engine = ProactiveEngine(...)
        await engine.start()
        # 在 ActivityTracker 初始化时注册回调：
        tracker = ActivityTracker(..., on_activity_change=engine.on_activity_change)
    """

    def __init__(
        self,
        db_path: str,
        ws_channel: Optional["LocalWSChannel"] = None,
        session_manager: Optional["SessionManager"] = None,
        llm_provider=None,
        soul_content: str = "",
        patrol_interval: int = PATROL_INTERVAL_SEC,
        buffer_merge_window: int = BUFFER_MERGE_WINDOW_SEC,
    ):
        self.db_path = db_path
        self.soul_content = soul_content
        self.patrol_interval = patrol_interval

        # ── 子模块 ──
        self.trigger_detector = TriggerDetector(db_path)
        self.thought_buffer = ThoughtBuffer(merge_window_sec=buffer_merge_window)
        self.gate = GateKeeper(db_path)
        self.strategies = StrategySelector(soul_content)
        self.generator = ProactiveMessageGenerator(llm_provider, soul_content)
        self.dispatcher = Dispatcher(ws_channel, session_manager, db_path)
        self.experience = ExperienceLearner(self.gate)

        # ── 运行时状态 ──
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

    # ═══════════════════════════════════════════════════════════════════
    #  启动 / 停止
    # ═══════════════════════════════════════════════════════════════════

    async def start(self) -> None:
        """启动引擎（三个后台任务）"""
        if self._running:
            logger.warning("ProactiveEngine already running")
            return

        self._loop = asyncio.get_running_loop()
        self._running = True

        self._tasks = [
            asyncio.create_task(self._system1_consumer(), name="proactive_s1"),
            asyncio.create_task(self._system2_patrol(), name="proactive_s2"),
            asyncio.create_task(self._buffer_flusher(), name="proactive_buf"),
            asyncio.create_task(self._feedback_checker(), name="proactive_fb"),
        ]

        logger.info(
            f"🚀 ProactiveEngine started "
            f"(patrol={self.patrol_interval}s, "
            f"buffer={self.thought_buffer.merge_window}s)"
        )

    async def stop(self) -> None:
        """停止引擎"""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        self.thought_buffer.clear()
        logger.info("ProactiveEngine stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ═══════════════════════════════════════════════════════════════════
    #  System 1：Collector 回调入口（同步→async 桥接）
    # ═══════════════════════════════════════════════════════════════════

    def on_activity_change(self, segment_data: dict) -> None:
        """
        ActivityTracker 活动段切换时的回调。

        这是同步方法（Collector 运行在独立线程），
        通过 call_soon_threadsafe 桥接到 async 事件队列。
        """
        if self._loop and self._running:
            self._loop.call_soon_threadsafe(
                self._event_queue.put_nowait,
                ("activity_change", segment_data),
            )

    def on_blacklist_hit(self, app_info: dict) -> None:
        """BlacklistMonitor 检测到黑名单应用时的回调"""
        logger.info(f"📥 on_blacklist_hit called: {app_info}, loop={self._loop is not None}, running={self._running}")
        if self._loop and self._running:
            self._loop.call_soon_threadsafe(
                self._event_queue.put_nowait,
                ("blacklist_hit", app_info),
            )
        else:
            logger.warning(f"⚠️ on_blacklist_hit 被忽略：loop={self._loop}, running={self._running}")

    def on_idle_change(self, is_idle: bool, idle_seconds: int) -> None:
        """IdleDetector 空闲状态变化时的回调"""
        if self._loop and self._running:
            self._loop.call_soon_threadsafe(
                self._event_queue.put_nowait,
                ("idle_change", {"is_idle": is_idle, "seconds": idle_seconds}),
            )

    def on_emotion_shift(self, emotion_context: dict) -> None:
        """EmotionTrigger 检测到情绪突变时的回调"""
        if self._loop and self._running:
            self._loop.call_soon_threadsafe(
                self._event_queue.put_nowait,
                ("emotion_shift", emotion_context),
            )

    # ═══════════════════════════════════════════════════════════════════
    #  System 1：async 事件消费者
    # ═══════════════════════════════════════════════════════════════════

    async def _system1_consumer(self) -> None:
        """消费 Collector 回调事件队列，检测实时触发"""
        logger.info("System 1 consumer started")
        while self._running:
            try:
                event_type, data = await asyncio.wait_for(
                    self._event_queue.get(), timeout=EVENT_CONSUME_TIMEOUT_SEC
                )
                triggers = self.trigger_detector.detect_realtime(event_type, data)
                for trigger in triggers:
                    self.thought_buffer.add(trigger)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"System 1 error: {e}", exc_info=True)

    # ═══════════════════════════════════════════════════════════════════
    #  System 2：定时巡检
    # ═══════════════════════════════════════════════════════════════════

    async def _system2_patrol(self) -> None:
        """每 N 秒查 DB，做深度分析"""
        logger.info(f"System 2 patrol started (interval={self.patrol_interval}s)")
        # 首次启动延迟，等系统稳定
        await asyncio.sleep(PATROL_INITIAL_DELAY_SEC)

        while self._running:
            try:
                # 清除上轮的话题抑制
                self.experience.clear_suppression()
                # 查 DB 获取最近活动
                context = self._gather_context_from_db()
                triggers = self.trigger_detector.detect_deep(context)
                for trigger in triggers:
                    self.thought_buffer.add(trigger)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"System 2 patrol error: {e}", exc_info=True)

            await asyncio.sleep(self.patrol_interval)

    # ═══════════════════════════════════════════════════════════════════
    #  ThoughtBuffer 消费者
    # ═══════════════════════════════════════════════════════════════════

    async def _buffer_flusher(self) -> None:
        """定期检查 ThoughtBuffer，合并后处理"""
        logger.info("Buffer flusher started")
        while self._running:
            try:
                if self.thought_buffer.is_ready():
                    trigger = self.thought_buffer.flush()
                    if trigger:
                        await self._process_trigger(trigger)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Buffer flusher error: {e}", exc_info=True)

            await asyncio.sleep(BUFFER_CHECK_INTERVAL_SEC)

    # ═══════════════════════════════════════════════════════════════════
    #  反馈超时检查
    # ═══════════════════════════════════════════════════════════════════

    async def _feedback_checker(self) -> None:
        """定期检查是否有主动消息超时未收到反馈"""
        logger.info("Feedback checker started")
        while self._running:
            try:
                timed_out = self.experience.check_timeouts()
                if timed_out:
                    logger.debug(f"Feedback timeout: {timed_out}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Feedback checker error: {e}", exc_info=True)

            await asyncio.sleep(FEEDBACK_CHECK_INTERVAL_SEC)

    # ═══════════════════════════════════════════════════════════════════
    #  统一处理流程
    # ═══════════════════════════════════════════════════════════════════

    async def _process_trigger(self, trigger: Trigger) -> None:
        """门控 → 抑制检查 → 策略选择 → LLM 生成 → 分发 → 记录"""
        logger.info(f"🔄 _process_trigger: type={trigger.type}, urgency={trigger.urgency:.2f}, context={trigger.context}")

        # 1. 门控：该不该说？（纯规则，零 token）
        passed, reason = self.gate.should_speak(trigger)
        if not passed:
            logger.info(f"🚫 Gate blocked: {trigger.type} ({reason})")
            return

        # 2. 话题抑制检查：上轮 ACCEPT 过的话题不再提
        if self.experience.is_topic_suppressed(trigger.type):
            logger.info(f"🔇 Topic suppressed: {trigger.type}")
            return

        logger.info(f"✅ Gate passed! Generating message for trigger={trigger.type}")

        # 3. 策略选择：说什么类型的话？
        context = self._gather_context_from_db()
        strategy = self.strategies.select(trigger, context)

        # 4. LLM 生成：具体说什么？（仅此步消耗 token）
        message, emotion = await self.generator.generate(
            strategy=strategy,
            trigger=trigger,
            context=context,
            soul_content=self.soul_content,
        )

        # 5. 分发：推送前端 + 写入会话历史
        event_id = await self.dispatcher.dispatch(
            message=message,
            trigger=trigger,
            strategy=strategy,
            emotion=emotion,
        )

        # 6. 记录到门控（冷却计时）
        self.gate.record_proactive(trigger.type, strategy)

        # 7. 注册到经验系统（等待反馈）
        self.experience.register_proactive_event(event_id, trigger.type, strategy)

        logger.info(
            f"✅ Proactive message sent: "
            f"trigger={trigger.type}, strategy={strategy}, "
            f"event_id={event_id}"
        )

    # ═══════════════════════════════════════════════════════════════════
    #  上下文收集
    # ═══════════════════════════════════════════════════════════════════

    def _gather_context_from_db(self) -> dict:
        """从 SQLite 查询最近活动，并注入用户画像与实时状态，构建完整上下文。"""
        # ── 1. 窗口活动（原有逻辑）────────────────────────────────────────
        rows = []
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(f"""
                SELECT process_name, window_title, app_category,
                       duration_sec, started_at
                FROM window_activities
                WHERE started_at > datetime('now', '-{DB_LOOKBACK_HOURS} hours')
                  AND is_synced != -1
                ORDER BY started_at DESC
                LIMIT {DB_LOOKBACK_LIMIT}
            """).fetchall()
            conn.close()
        except Exception as e:
            logger.error(f"DB query failed: {e}")

        # ── 2. 用户已知事实（FactMemory）──────────────────────────────────
        user_facts: list[str] = []
        try:
            from memory.fact_memory import get_fact_memory
            fm = get_fact_memory()
            facts = fm.get_all_facts(limit=20)
            user_facts = [f["content"] for f in facts if f.get("content")]
        except Exception as e:
            logger.debug(f"FactMemory 读取跳过: {e}")

        # ── 3. 实时工作状态（WorkingMemory）──────────────────────────────
        working_summary: dict = {}
        try:
            from memory.working_memory import get_working_memory
            wm = get_working_memory()
            working_summary = wm.get_session_summary()
        except Exception as e:
            logger.debug(f"WorkingMemory 读取跳过: {e}")

        return {
            "recent_activities": rows,
            "current_time": datetime.now().isoformat(),
            "user_facts": user_facts,
            "working_summary": working_summary,
        }

    # ═══════════════════════════════════════════════════════════════════
    #  外部接口
    # ═══════════════════════════════════════════════════════════════════

    def on_user_message(self, text: str) -> None:
        """
        用户发消息时调用（由 LocalWSChannel 触发）。
        检查是否是对主动消息的回复，处理反馈。
        """
        if not self.experience.get_pending_count():
            return

        # 最近的主动消息 event_id
        pending = self.experience._pending_feedback
        if not pending:
            return

        # 取最近的一条待反馈消息
        latest_id = max(pending.keys(), key=lambda k: pending[k]["sent_at"])
        feedback = ExperienceLearner.classify_user_response(text)
        self.experience.process_feedback(latest_id, feedback)

    def get_stats(self) -> dict:
        """获取引擎状态（用于调试/API）"""
        return {
            "running": self._running,
            "buffer_size": self.thought_buffer.size,
            "pending_feedback": self.experience.get_pending_count(),
            "gate": self.gate.get_stats(),
        }
