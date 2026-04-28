"""
DbWriteQueue - 异步 SQLite 写入队列
参考 Screenpipe write_queue.rs 设计思路，Python 实现

采集线程只调用 put()，不阻塞。
专用后台线程批量 executemany 写入，启用 WAL 模式。
"""
import logging
import queue
import sqlite3
import threading
from typing import Any, Tuple

logger = logging.getLogger(__name__)

_SENTINEL = object()  # 停止信号


class DbWriteQueue:
    """
    SQLite 异步批量写入队列

    用法：
        wq = DbWriteQueue(db_path)
        wq.put("INSERT INTO t (a) VALUES (?)", (val,))
        wq.flush()   # 程序退出前调用，等待队列清空
        wq.close()   # 关闭写线程和连接
    """

    def __init__(self, db_path: str, batch_size: int = 20, flush_interval: float = 2.0):
        self._db_path = db_path
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._conn: sqlite3.Connection | None = None
        self._thread = threading.Thread(
            target=self._write_loop,
            name="DbWriteQueue",
            daemon=True,
        )
        self._thread.start()

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    def put(self, sql: str, params: tuple):
        """非阻塞入队，立即返回"""
        self._queue.put((sql, params))

    def flush(self):
        """等待队列中所有挂起写入完成（程序退出时调用）"""
        self._queue.join()

    def close(self):
        """停止写线程并关闭数据库连接"""
        self._stop_event.set()
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=5.0)
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ──────────────────────────────────────────────
    # 内部实现
    # ──────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """获取（或初始化）持久化数据库连接，启用 WAL 模式（改进 3）"""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.commit()
            logger.info(f"DbWriteQueue: WAL 连接已建立 → {self._db_path}")
        return self._conn

    def _write_loop(self):
        logger.info("DbWriteQueue: 写入线程启动")
        while not self._stop_event.is_set():
            batch = []
            try:
                # 阻塞等待第一条任务
                first = self._queue.get(timeout=self._flush_interval)
                if first is _SENTINEL:
                    self._queue.task_done()
                    break
                batch.append(first)
                self._queue.task_done()

                # 非阻塞凑批次
                while len(batch) < self._batch_size:
                    try:
                        item = self._queue.get_nowait()
                        if item is _SENTINEL:
                            self._queue.task_done()
                            self._stop_event.set()
                            break
                        batch.append(item)
                        self._queue.task_done()
                    except queue.Empty:
                        break

                if batch:
                    self._execute_batch(batch)

            except queue.Empty:
                continue
            except Exception as e:
                logger.exception(f"DbWriteQueue: 写入线程异常: {e}")

        logger.info("DbWriteQueue: 写入线程已停止")

    def _execute_batch(self, batch: list):
        conn = self._get_conn()
        try:
            with conn:
                for sql, params in batch:
                    conn.execute(sql, params)
            logger.debug(f"DbWriteQueue: 批量写入 {len(batch)} 条")
        except Exception as e:
            logger.exception(f"DbWriteQueue: 批量写入失败，尝试逐条重试: {e}")
            for sql, params in batch:
                try:
                    with conn:
                        conn.execute(sql, params)
                except Exception as e2:
                    logger.error(f"DbWriteQueue: 单条写入失败: {e2} | {sql} | {params}")
