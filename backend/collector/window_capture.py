"""
WindowActivityCapture - 窗口活动采集组件
每分钟记录当前活跃窗口的标题、进程名、时长
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

import psutil
import win32gui
import win32process

from collector.base import BaseCaptureComponent

logger = logging.getLogger(__name__)


def get_active_window() -> Optional[Dict]:
    """获取当前活跃窗口信息"""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None

        title = win32gui.GetWindowText(hwnd)
        if not title:
            return None

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            process = psutil.Process(pid)
            process_name = process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process_name = "unknown"

        return {
            "window_title": title,
            "process_name": process_name,
            "pid": pid,
            "captured_at": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.debug(f"获取活跃窗口失败: {e}")
        return None


class WindowActivityCapture(BaseCaptureComponent):
    """
    窗口活动采集组件
    每次采集记录当前活跃窗口，交由 ActivityTracker 处理活动段逻辑
    """

    def __init__(self, capture_interval: float = 60.0):
        super().__init__(
            name="WindowActivityCapture",
            capture_interval=capture_interval
        )

    def _capture_impl(self) -> List[Dict]:
        window_info = get_active_window()
        if window_info:
            logger.debug(
                f"采集: [{window_info['process_name']}] {window_info['window_title'][:50]}"
            )
            return [window_info]
        return []
