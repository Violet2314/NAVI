"""
ScreenshotCapture - 截图采集组件
参考 MineContext 的 ScreenshotCapture 实现 + Screenpipe 优化

去重（两步，参考 Screenpipe frame_comparison.rs）：
  Step 1 - raw bytes MD5 快速跳出（零成本，连 PIL 都不创建）   ← 改进 1
  Step 2 - 1/4 降采样直方图 Hellinger 距离（高精度，低成本）  ← 改进 4

写入：DbWriteQueue 异步批量写入，不阻塞采集主循环              ← 改进 2 & 3
"""
import hashlib
import logging
import math
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

from collector.base import BaseCaptureComponent
from db.write_queue import DbWriteQueue

logger = logging.getLogger(__name__)


def _histogram_diff(prev_small: Image.Image, curr_img: Image.Image) -> float:
    """
    Hellinger 距离比对（参考 Screenpipe frame_comparison.rs）
    prev_small : 上一帧已缓存的 1/4 灰度图
    curr_img   : 当前帧原始 PIL Image（函数内部缩放）
    返回 0.0（完全相同）~ 1.0（完全不同）
    """
    w, h = curr_img.size
    curr_small = curr_img.resize(
        (max(1, w // 4), max(1, h // 4)), Image.Resampling.NEAREST
    ).convert("L")

    # 分辨率变化时同步 prev 大小
    if prev_small.size != curr_small.size:
        prev_small = prev_small.resize(curr_small.size, Image.Resampling.NEAREST)

    hist1 = prev_small.histogram()
    hist2 = curr_small.histogram()
    total = sum(hist1)
    if total == 0:
        return 0.0
    bc = sum(math.sqrt((hist1[i] / total) * (hist2[i] / total)) for i in range(256))
    return math.sqrt(max(0.0, 1.0 - bc))


class ScreenshotCapture(BaseCaptureComponent):
    """
    截图采集组件
    - 使用 mss 截图，支持多显示器
    - 两步去重：MD5 快速跳出 + 1/4 降采样直方图精确比对
    - 异步写入：DbWriteQueue，不阻塞采集线程
    - 窗口切换时可强制截图（force_capture）
    """

    def __init__(
        self,
        screenshot_dir: str,
        db_path: str,
        device_id: str = "home_pc",
        capture_interval: float = 30.0,
        format: str = "jpg",
        quality: int = 75,
        histogram_threshold: float = 0.05,     # Hellinger 阈值，低于此值跳过（改进 4）
        max_image_size: int = 1280,
    ):
        super().__init__(name="ScreenshotCapture", capture_interval=capture_interval)
        self._screenshot_dir = screenshot_dir
        self._db_path = db_path
        self._device_id = device_id
        self._format = format
        self._quality = quality
        self._histogram_threshold = histogram_threshold
        self._max_image_size = max_image_size

        # 两步去重状态（改进 1 & 4）
        self._last_raw_hashes: Dict[str, str] = {}      # monitor_id → raw MD5
        self._last_small_imgs: Dict[str, Image.Image] = {}  # monitor_id → 1/4 灰度图缓存

        # 异步写入队列（改进 2 & 3）
        self._write_queue = DbWriteQueue(db_path)

    def _start_impl(self) -> bool:
        Path(self._screenshot_dir).mkdir(parents=True, exist_ok=True)
        return True

    def _stop_impl(self, graceful: bool = True) -> bool:
        if graceful:
            self._write_queue.flush()
        self._write_queue.close()
        return True

    def force_capture(self):
        """强制立即截图（窗口切换时调用）"""
        results = self._do_capture(force=True)
        if results and self._callback:
            self._callback(results)

    def _capture_impl(self) -> List[Dict]:
        return self._do_capture(force=False)

    def _do_capture(self, force: bool = False) -> List[Dict]:
        try:
            import mss
            results = []
            now = datetime.now()
            date_dir = Path(self._screenshot_dir) / now.strftime("%Y-%m-%d")
            date_dir.mkdir(parents=True, exist_ok=True)

            with mss.mss() as sct:
                monitors = sct.monitors[1:]  # 跳过 monitors[0]（所有屏幕合并）
                for i, monitor in enumerate(monitors):
                    monitor_id = f"monitor_{i+1}"
                    sct_img = sct.grab(monitor)

                    # ── Step 1: raw bytes MD5 快速跳出（改进 1）────────────────
                    # 完全相同的帧连 PIL Image 都不创建，直接跳过
                    raw_bytes = bytes(sct_img.raw)
                    raw_hash = hashlib.md5(raw_bytes).hexdigest()
                    last_raw = self._last_raw_hashes.get(monitor_id, "")
                    if last_raw and raw_hash == last_raw and not force:
                        logger.debug(f"{monitor_id}: MD5 命中，跳过")
                        continue

                    # ── Step 2: 1/4 降采样直方图比对（改进 4）──────────────────
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    last_small = self._last_small_imgs.get(monitor_id)
                    if last_small and not force:
                        diff = _histogram_diff(last_small, img)
                        if diff < self._histogram_threshold:
                            logger.debug(f"{monitor_id}: 直方图相似 diff={diff:.4f}，跳过")
                            # 更新 raw hash 避免下次还触发 PIL 创建
                            self._last_raw_hashes[monitor_id] = raw_hash
                            continue

                    # ── 缓存 1/4 灰度图供下次比对（节省内存）────────────────────
                    w, h = img.size
                    self._last_small_imgs[monitor_id] = img.resize(
                        (max(1, w // 4), max(1, h // 4)), Image.Resampling.NEAREST
                    ).convert("L")
                    self._last_raw_hashes[monitor_id] = raw_hash

                    # ── 保存截图（resize 长边至 max_image_size 节省 token）────────
                    if self._max_image_size > 0 and (w > self._max_image_size or h > self._max_image_size):
                        save_img = img.copy()
                        save_img.thumbnail(
                            (self._max_image_size, self._max_image_size),
                            Image.Resampling.BILINEAR,
                        )
                    else:
                        save_img = img

                    timestamp_str = now.strftime("%H%M%S")
                    filename = f"{timestamp_str}_{monitor_id}.{self._format}"
                    filepath = date_dir / filename

                    buf = BytesIO()
                    if self._format in ("jpg", "jpeg"):
                        save_img.save(buf, format="JPEG", quality=self._quality, optimize=True)
                    else:
                        save_img.save(buf, format="PNG", optimize=True, compress_level=6)

                    with open(filepath, "wb") as f:
                        f.write(buf.getvalue())

                    file_size_kb = filepath.stat().st_size // 1024

                    # ── 异步写入数据库（改进 2 & 3）──────────────────────────────
                    self._write_queue.put(
                        "INSERT INTO screenshots (captured_at, file_path, file_size_kb, device_id)"
                        " VALUES (?, ?, ?, ?)",
                        (now.isoformat(), str(filepath), file_size_kb, self._device_id),
                    )

                    record = {
                        "captured_at": now.isoformat(),
                        "file_path": str(filepath),
                        "file_size_kb": file_size_kb,
                        "device_id": self._device_id,
                    }
                    results.append(record)
                    logger.debug(f"截图保存：{filepath} ({file_size_kb}KB)")

            return results
        except Exception as e:
            logger.exception(f"截图采集失败: {e}")
            return []