"""
image_compress.py - 图片压缩工具
上传给 Vision LLM 前先压缩，节省 token

使用方式：
    from utils.image_compress import compress_for_llm

    b64 = compress_for_llm("path/to/image.png")
    # 直接传给 LLM 的 image_url content
"""
import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger("navi.image_compress")

# 默认压缩参数
DEFAULT_MAX_SIDE = 1024     # 长边最大像素
DEFAULT_QUALITY = 75        # JPEG 质量（1-95）
DEFAULT_FORMAT = "JPEG"     # 统一转为 JPEG


def compress_image(
    image_path: str | Path,
    max_side: int = DEFAULT_MAX_SIDE,
    quality: int = DEFAULT_QUALITY,
    output_format: str = DEFAULT_FORMAT,
) -> Optional[bytes]:
    """
    压缩图片，返回压缩后的二进制数据

    Args:
        image_path: 图片路径
        max_side: 长边最大像素，超过则等比缩小
        quality: JPEG 质量（1-95）
        output_format: 输出格式，默认 JPEG

    Returns:
        压缩后的图片字节，失败返回 None
    """
    try:
        img = Image.open(image_path).convert("RGB")
        original_size = Path(image_path).stat().st_size // 1024

        # 等比缩放：长边超过 max_side 才缩
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            new_w = int(w * scale)
            new_h = int(h * scale)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            logger.debug(f"缩放：{w}x{h} → {new_w}x{new_h}")

        # 压缩到字节流
        buf = BytesIO()
        img.save(buf, format=output_format, quality=quality, optimize=True)
        compressed_bytes = buf.getvalue()

        compressed_size = len(compressed_bytes) // 1024
        logger.debug(
            f"压缩完成：{original_size}KB → {compressed_size}KB "
            f"({image_path})"
        )
        return compressed_bytes

    except Exception as e:
        logger.warning(f"图片压缩失败：{image_path} - {e}")
        return None


def compress_for_llm(
    image_path: str | Path,
    max_side: int = DEFAULT_MAX_SIDE,
    quality: int = DEFAULT_QUALITY,
) -> Optional[str]:
    """
    压缩图片并转为纯 base64 字符串（不含 data: 前缀）

    Returns:
        纯 base64 字符串，失败返回 None
    """
    compressed = compress_image(image_path, max_side=max_side, quality=quality)
    if compressed is None:
        return None
    return base64.b64encode(compressed).decode("utf-8")


def compress_bytes_for_llm(
    image_bytes: bytes,
    max_side: int = DEFAULT_MAX_SIDE,
    quality: int = DEFAULT_QUALITY,
) -> Optional[str]:
    """
    从内存中的图片字节压缩并转为纯 base64（用于截图场景）

    Returns:
        纯 base64 字符串，失败返回 None
    """
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")

        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    except Exception as e:
        logger.warning(f"内存图片压缩失败：{e}")
        return None
