"""
MemoryScheduler - 记忆系统统一调度器。

每 N 分钟执行一次巡检：
  1. 检查 FactMemory ingestion 队列积压
  2. EpisodicMemory compaction（超过阈值时清理旧条目）
  3. FactMemory superseded 链 GC（保留最近 N 个版本）
  4. 触发 UserModel 增量渲染（如果有新 fact）
"""
import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger("navi.memory.scheduler")


class MemoryScheduler:
    def __init__(self, workspace: Path, interval_sec: int = 300):
        self.workspace = workspace
        self.interval = interval_sec
        self._task: asyncio.Task | None = None
        self._last_render_at: float = 0.0

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="memory-scheduler")
        logger.info("MemoryScheduler 启动，巡检间隔 %ds", self.interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("MemoryScheduler tick 失败: %s", e)

    async def _tick(self) -> None:
        t0 = time.monotonic()

        # 1. 检查 ingestion 队列积压
        try:
            from memory.fact_memory import get_fact_memory
            fm = get_fact_memory()
            backlog = fm.ingestion_backlog()
            if backlog > 50:
                logger.warning("FactMemory ingestion 积压: %d 条待处理", backlog)
            elif backlog > 0:
                logger.debug("FactMemory ingestion 队列: %d 条", backlog)
        except Exception as e:
            logger.debug("FactMemory 积压检查跳过: %s", e)

        # 2. EpisodicMemory compaction
        try:
            from memory.episodic_memory import get_episodic_memory
            em = get_episodic_memory()
            size = em.collection_size()
            if size > 10000:
                removed = await em.compact_old_entries(older_than_days=90)
                if removed:
                    logger.info("EpisodicMemory compaction: 清理 %d 条旧条目", removed)
        except Exception as e:
            logger.debug("EpisodicMemory compaction 跳过: %s", e)

        # 3. FactMemory superseded 链 GC
        try:
            gc_count = fm.gc_superseded_chains(keep_versions=3)
            if gc_count:
                logger.info("FactMemory GC: 清理 %d 条过期 superseded 链", gc_count)
        except Exception as e:
            logger.debug("FactMemory GC 跳过: %s", e)

        # 4. UserModel 增量渲染
        try:
            from memory.user_model import get_user_model_manager
            um = get_user_model_manager(self.workspace)
            if fm.has_new_facts_since(self._last_render_at):
                um.render_from_facts()
                self._last_render_at = time.time()
                logger.info("UserModel 增量渲染完成")
        except Exception as e:
            logger.debug("UserModel 渲染跳过: %s", e)

        elapsed = time.monotonic() - t0
        logger.debug("MemoryScheduler tick 完成 (%.1fs)", elapsed)
