"""
IdleDetector - 用户空闲检测
参考 Screenpipe idle_detector.rs 的设计思路，用 Windows GetLastInputInfo 实现

检测逻辑：
  - 连续 N 秒无键盘/鼠标输入 → 判定为「空闲」
  - 恢复输入后立即退出空闲状态
  - 空闲状态下活动段应被截断，避免「发呆也算工作」的问题

与 Screenpipe 的区别：
  Screenpipe 的 IdleDetector 检测 CPU 空闲（给音频转录调度用）
  Navi 的 IdleDetector 检测用户输入空闲（给活动追踪用）
  原理一致：都是「低于阈值持续一段时间才算真正空闲」
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Windows 专用：获取上次键鼠输入距今的毫秒数
_win32_available = False
try:
    import ctypes
    import ctypes.wintypes

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.UINT),
            ("dwTime", ctypes.wintypes.DWORD),
        ]

    _win32_available = True
except Exception:
    pass


def _get_idle_seconds_windows() -> float:
    """
    使用 Windows GetLastInputInfo API 获取距上次键鼠输入的秒数。
    参考 Screenpipe 的 Windows idle time 实现思路。
    """
    try:
        last_input = _LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input))
        # GetTickCount 单位毫秒，与 dwTime 一致
        current_tick = ctypes.windll.kernel32.GetTickCount64()
        # dwTime 是 DWORD(32位)，GetTickCount64 是 64 位，按 32 位掩码对齐差值
        elapsed_ms = (current_tick - last_input.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except Exception as e:
        logger.debug(f"GetLastInputInfo 失败: {e}")
        return 0.0


def get_idle_seconds() -> float:
    """
    获取用户空闲秒数（距上次键盘/鼠标输入）。
    Windows 使用 GetLastInputInfo，其他平台返回 0（暂不支持）。
    """
    if _win32_available:
        return _get_idle_seconds_windows()
    return 0.0


class IdleDetector:
    """
    用户空闲状态检测器。

    参考 Screenpipe IdleDetector 的「需持续低于阈值才算空闲」设计：
      - 不是瞬间空闲就切断，避免误判（比如短暂离开去喝水）
      - 超过 idle_threshold_sec 秒无输入 → is_idle() = True
      - 有新输入 → 立即恢复 is_idle() = False

    用法（在 ActivityTracker 里调用）：
        detector = IdleDetector(idle_threshold_sec=300)
        if detector.is_idle():
            # 结束当前活动段，标记为空闲
    """

    def __init__(self, idle_threshold_sec: float = 300.0):
        """
        Args:
            idle_threshold_sec: 多少秒无输入才认为是空闲，默认 300 秒（5分钟）
        """
        self._threshold = idle_threshold_sec
        self._was_idle: bool = False  # 上次检测时是否空闲
        self._idle_start: Optional[float] = None  # 空闲开始的 time.monotonic() 时间点

    def check(self) -> tuple[bool, float]:
        """
        检测当前是否空闲。

        Returns:
            (is_idle, idle_seconds)
            is_idle     : 当前是否处于空闲状态
            idle_seconds: 已空闲多少秒（未空闲时为 0）
        """
        idle_sec = get_idle_seconds()
        is_idle = idle_sec >= self._threshold

        if is_idle and not self._was_idle:
            # 刚刚进入空闲
            self._idle_start = time.monotonic() - idle_sec
            logger.info(f"IdleDetector: 进入空闲状态（已 {idle_sec:.0f}s 无输入，阈值 {self._threshold}s）")

        if not is_idle and self._was_idle:
            # 从空闲中恢复
            total = time.monotonic() - (self._idle_start or time.monotonic())
            logger.info(f"IdleDetector: 恢复活跃（空闲了约 {total:.0f}s）")
            self._idle_start = None

        self._was_idle = is_idle
        return is_idle, idle_sec if is_idle else 0.0

    def is_idle(self) -> bool:
        """快速判断当前是否空闲（不记录状态变化日志）"""
        return get_idle_seconds() >= self._threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float):
        self._threshold = value
