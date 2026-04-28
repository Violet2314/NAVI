"""
BlacklistMonitor - 学习模式黑名单守卫
每隔 N 秒扫描运行中的进程，若发现黑名单进程且学习模式开启，立即终止。
学习模式状态通过 set_study_mode() 热切换，无需重启。
"""
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Set

from blacklist.enforcer import kill_process

logger = logging.getLogger(__name__)

# 扫描间隔（秒）
_SCAN_INTERVAL = 5


def _get_running_processes() -> Set[str]:
    """获取当前所有运行中的进程名（小写）"""
    try:
        import subprocess
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=5,
        )
        names = set()
        for line in result.stdout.splitlines():
            line = line.strip().strip('"')
            if "," in line:
                name = line.split(",")[0].strip().strip('"').lower()
                if name:
                    names.add(name)
        return names
    except Exception as e:
        logger.debug(f"tasklist 失败: {e}")
        return set()


class BlacklistMonitor:
    """
    黑名单监控器。
    - 从 DB 读取 is_blacklist=1 的进程名作为黑名单
    - study_mode=True 时自动杀黑名单进程
    - 支持热更新黑名单和学习模式状态
    """

    def __init__(self, db_path: str, scan_interval: float = _SCAN_INTERVAL):
        self._db_path = db_path
        self._scan_interval = scan_interval
        self._study_mode = False
        self._blacklist: Set[str] = set()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._kill_log: list[dict] = []   # 最近 50 条击杀记录
        # 主动对话回调：检测到黑名单命中时通知 ProactiveEngine（可选）
        from typing import Callable, Optional
        self._on_hit: Optional[Callable] = None

    # ── 公开控制接口 ──────────────────────────────────────────────────────

    def start(self):
        """启动后台扫描线程"""
        self._stop_event.clear()
        self._reload_blacklist()
        self._thread = threading.Thread(
            target=self._scan_loop,
            name="BlacklistMonitor",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[BlacklistMonitor] 已启动，扫描间隔 {self._scan_interval}s")

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("[BlacklistMonitor] 已停止")

    def set_study_mode(self, enabled: bool):
        """热切换学习模式（即时生效）"""
        with self._lock:
            self._study_mode = enabled
        status = "🔒 开启" if enabled else "🔓 关闭"
        logger.info(f"[BlacklistMonitor] 学习模式 {status}")

    def is_study_mode(self) -> bool:
        with self._lock:
            return self._study_mode

    def reload_blacklist(self):
        """从 DB 重新加载黑名单（前端添加/删除后调用）"""
        self._reload_blacklist()

    def get_status(self) -> dict:
        with self._lock:
            return {
                "study_mode": self._study_mode,
                "blacklist": sorted(self._blacklist),
                "recent_kills": self._kill_log[-10:],
            }

    # ── 内部实现 ──────────────────────────────────────────────────────────

    def _reload_blacklist(self):
        """从 app_rules 表加载 is_blacklist=1 的进程"""
        try:
            conn = sqlite3.connect(self._db_path)
            rows = conn.execute(
                "SELECT process_name FROM app_rules WHERE is_blacklist=1"
            ).fetchall()
            conn.close()
            new_bl = {r[0].lower() for r in rows}
            with self._lock:
                self._blacklist = new_bl
            logger.info(f"[BlacklistMonitor] 已加载 {len(new_bl)} 个黑名单进程: {new_bl}")
        except Exception as e:
            logger.warning(f"[BlacklistMonitor] 加载黑名单失败: {e}")

    def _scan_loop(self):
        logger.info("[BlacklistMonitor] 扫描线程启动")
        while not self._stop_event.is_set():
            with self._lock:
                if self._study_mode and self._blacklist:
                    blacklist_snapshot = set(self._blacklist)
                else:
                    blacklist_snapshot = set()

            if blacklist_snapshot:
                running = _get_running_processes()
                hits = running & blacklist_snapshot
                for proc in hits:
                    ok, msg = kill_process(proc)
                    logger.warning(f"[学习模式] {msg}")
                    with self._lock:
                        self._kill_log.append({
                            "process": proc,
                            "success": ok,
                            "msg": msg,
                            "time": time.strftime("%H:%M:%S"),
                        })
                        # 最多保留 50 条
                        if len(self._kill_log) > 50:
                            self._kill_log = self._kill_log[-50:]

                    # 广播事件到所有 WebSocket 客户端（Live2D 联动）
                    if ok:
                        try:
                            from bus.broadcaster import get_broadcaster
                            get_broadcaster().broadcast_sync({
                                "type": "navi:blacklist_kill",
                                "process": proc,
                                "emotion": "angry",
                                "message": f"又在偷偷开 {proc}？学习模式下不许摸鱼！",
                                "time": time.strftime("%H:%M:%S"),
                            })
                        except Exception as be:
                            logger.debug(f"[BlacklistMonitor] 广播失败: {be}")

                        # 通知主动对话引擎（如果已注册回调）
                        if self._on_hit:
                            try:
                                self._on_hit({
                                    "process": proc,
                                    "time": time.strftime("%H:%M:%S"),
                                })
                            except Exception as pe:
                                logger.debug(f"[BlacklistMonitor] 主动对话回调失败: {pe}")

            self._stop_event.wait(self._scan_interval)
        logger.info("[BlacklistMonitor] 扫描线程停止")
