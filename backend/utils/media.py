"""
媒体文件持久化工具
将临时图片复制到 {data_dir}/media/ 目录，返回文件名供 DB 存储和前端访问。
前端通过 /media/{filename} 获取图片（FastAPI StaticFiles 挂载）。
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path

from config import get_config


def get_media_dir() -> Path:
    """获取（并确保存在）媒体存储目录"""
    cfg = get_config()
    media_dir = Path(cfg.data_dir) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir


def persist_image(src_path: str) -> str | None:
    """
    把一张图片复制到持久化 media 目录。
    
    Args:
        src_path: 源文件绝对路径（如 temp_screenshot.jpg）
    
    Returns:
        持久化后的文件名（如 '20260426_175200_a1b2c3.jpg'），失败返回 None
    """
    src = Path(src_path)
    if not src.exists() or not src.is_file():
        return None

    suffix = src.suffix.lower() or ".png"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    filename = f"{ts}_{short_id}{suffix}"

    dst = get_media_dir() / filename
    shutil.copy2(str(src), str(dst))
    return filename


def persist_images(src_paths: list[str]) -> list[str]:
    """批量持久化，返回成功的文件名列表"""
    result = []
    for p in src_paths:
        name = persist_image(p)
        if name:
            result.append(name)
    return result
