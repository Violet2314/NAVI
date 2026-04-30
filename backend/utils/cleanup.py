"""
cleanup.py - 截图文件 + DB 记录自动清理
策略：保留最近 N 天（默认3天），更早的文件和记录全部删除
"""
import logging
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("navi.cleanup")


def cleanup_screenshots(screenshot_dir: str, db_path: str, keep_days: int = 3) -> dict:
    """
    删除 keep_days 天前的截图文件和 DB 记录。
    返回清理统计信息。
    """
    cutoff = date.today() - timedelta(days=keep_days)
    cutoff_str = cutoff.isoformat()           # 'YYYY-MM-DD'
    stats = {"files_deleted": 0, "dirs_deleted": 0, "db_rows_deleted": 0, "freed_kb": 0}

    # ── 1. 删文件（按日期目录结构：screenshot_dir/YYYY-MM-DD/）
    base = Path(screenshot_dir)
    if base.exists():
        for day_dir in sorted(base.iterdir()):
            if not day_dir.is_dir():
                continue
            # 目录名格式 YYYY-MM-DD
            try:
                dir_date = date.fromisoformat(day_dir.name)
            except ValueError:
                continue
            if dir_date < cutoff:
                freed = sum(f.stat().st_size for f in day_dir.rglob("*") if f.is_file()) // 1024
                stats["freed_kb"] += freed
                stats["files_deleted"] += sum(1 for f in day_dir.rglob("*") if f.is_file())
                shutil.rmtree(day_dir)
                stats["dirs_deleted"] += 1
                logger.info(f"已删除截图目录：{day_dir}（释放 {freed} KB）")

    # ── 2. 清 DB 记录
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "DELETE FROM screenshots WHERE date(captured_at) < ?", (cutoff_str,)
        )
        stats["db_rows_deleted"] = cur.rowcount
        conn.commit()
        conn.close()
        if stats["db_rows_deleted"]:
            logger.info(f"已清理 DB 截图记录：{stats['db_rows_deleted']} 条")
    except Exception as e:
        logger.error(f"DB 清理失败：{e}")

    logger.info(
        f"✅ 清理完成 | 保留最近{keep_days}天 | "
        f"删除文件{stats['files_deleted']}个 "
        f"/ 目录{stats['dirs_deleted']}个 "
        f"/ DB记录{stats['db_rows_deleted']}条 "
        f"/ 释放{stats['freed_kb']}KB"
    )
    return stats
