"""
BaseCaptureComponent - 采集组件抽象基类
参考 MineContext 的 BaseCaptureComponent 模式实现
"""
import abc
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseCaptureComponent(abc.ABC):
    """
    采集组件抽象基类
    提供后台线程循环、回调机制、统计追踪、优雅停止
    子类只需实现 _capture_impl()
    """

    def __init__(self, name: str, capture_interval: float = 60.0):
        self._name = name
        self._capture_interval = capture_interval
        self._running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._callback: Optional[Callable[[List[Dict]], None]] = None
        self._lock = threading.RLock()

        # 统计
        self._capture_count = 0
        self._error_count = 0
        self._last_capture_time: Optional[datetime] = None
        self._last_error: Optional[str] = None

    def start(self) -> bool:
        with self._lock:
            if self._running:
                logger.warning(f"{self._name}: 已在运行中")
                return True
            try:
                if not self._start_impl():
                    return False
                self._running = True
                self._stop_event.clear()
                self._capture_thread = threading.Thread(
                    target=self._capture_loop,
                    name=f"{self._name}_thread",
                    daemon=True
                )
                self._capture_thread.start()
                logger.info(f"{self._name}: 启动成功，采集间隔 {self._capture_interval}s")
                return True
            except Exception as e:
                logger.exception(f"{self._name}: 启动失败: {e}")
                self._last_error = str(e)
                return False

    def stop(self, graceful: bool = True) -> bool:
        with self._lock:
            if not self._running:
                return True
            try:
                self._stop_event.set()
                if self._capture_thread and self._capture_thread.is_alive():
                    self._capture_thread.join(timeout=5.0)
                self._stop_impl(graceful=graceful)
                self._running = False
                logger.info(f"{self._name}: 已停止")
                return True
            except Exception as e:
                logger.exception(f"{self._name}: 停止失败: {e}")
                return False

    def set_callback(self, callback: Callable[[List[Dict]], None]):
        with self._lock:
            self._callback = callback

    def set_interval(self, interval_sec: float):
        """热重载采集间隔，立即生效（下一次 wait 会用新值）"""
        self._capture_interval = max(1.0, interval_sec)
        logger.info(f"{self._name}: 采集间隔热重载 → {self._capture_interval}s")

    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "running": self._running,
            "capture_count": self._capture_count,
            "error_count": self._error_count,
            "last_capture_time": self._last_capture_time.isoformat() if self._last_capture_time else None,
            "last_error": self._last_error,
        }

    def _capture_loop(self):
        """后台采集循环"""
        logger.info(f"{self._name}: 采集线程启动")
        while not self._stop_event.is_set():
            try:
                results = self._capture_impl()
                self._capture_count += 1
                self._last_capture_time = datetime.now()
                if results and self._callback:
                    self._callback(results)
            except Exception as e:
                logger.exception(f"{self._name}: 采集异常: {e}")
                self._last_error = str(e)
                self._error_count += 1
                self._stop_event.wait(max(1.0, self._capture_interval / 2))
                continue
            self._stop_event.wait(self._capture_interval)
        logger.info(f"{self._name}: 采集线程停止")

    # ---- 子类实现 ----

    def _start_impl(self) -> bool:
        return True

    def _stop_impl(self, graceful: bool = True) -> bool:
        return True

    @abc.abstractmethod
    def _capture_impl(self) -> List[Dict]:
        """执行一次采集，返回数据列表"""
