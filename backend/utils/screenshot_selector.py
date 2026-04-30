"""
screenshot_selector.py - 截图选图工具（Day 3 日报生成时使用）
从一天的截图中选出「内容变化最大的 N 张」发给 Vision LLM
参考 MineContext realtime_activity_monitor.py 的选图策略

用法（Day 3 日报生成时调用）：
    from utils.screenshot_selector import select_representative
    shots = select_representative(date_str="2026-04-19", top_n=10)
    # shots: [{"path": ..., "captured_at": ..., "importance": ...}, ...]
"""
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import date

logger = logging.getLogger("navi.utils.screenshot_selector")


def _extract_monitor_id(file_path: str) -> str:
    """
    从文件名中提取显示器ID。
    文件名格式：143052_monitor_1.jpg → 'monitor_1'
    无法识别则返回 'monitor_1'（单屏兼容）
    """
    stem = Path(file_path).stem          # e.g. '143052_monitor_2'
    parts = stem.split("_monitor_")
    return f"monitor_{parts[1]}" if len(parts) == 2 else "monitor_1"


def select_representative(
    date_str: str,
    screenshot_dir: str,
    db_path: str,
    top_n: int = 10,
) -> List[Dict]:
    """
    从指定日期的截图中选出「内容变化最大」的 top_n 组截图。

    双屏策略：
    1. 按时间 + 显示器分组（同一时刻的多块屏幕算「一组」）
    2. 每个显示器独立计算相邻帧的 dHash Hamming 距离
    3. 同一组的「重要性」取各显示器变化量的最大值
    4. 按组重要性排序，选出 top_n 组
    5. 选出的组内所有显示器截图都返回（双屏 top_n=30 → 最多60张）
    6. 按时间顺序重排返回（保持叙事顺序）

    Returns:
        List[Dict]: [{"path": str, "captured_at": str, "importance": int, "monitor": str}, ...]
    """
    import sqlite3
    from PIL import Image

    def _image_hash(img: Image.Image) -> int:
        """dHash：将图像缩小到 9×8，计算横向梯度得到 64-bit 哈希。"""
        gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(gray.getdata())  # type: ignore[arg-type]
        bits = [1 if pixels[i] > pixels[i + 1] else 0
                for i in range(8 * 8)
                if (i % 9) < 8]
        h = 0
        for b in bits:
            h = (h << 1) | b
        return h

    def _hamming_distance(a: int, b: int) -> int:
        """计算两个 dHash 的 Hamming 距离（不同 bit 的数量）。"""
        return bin(a ^ b).count("1")

    # ── 1. 从 DB 读取当天截图列表
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """
            SELECT file_path, captured_at FROM screenshots
            WHERE date(captured_at) = ?
            ORDER BY captured_at ASC
            """,
            (date_str,),
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"读取截图列表失败：{e}")
        return []

    if not rows:
        logger.info(f"{date_str} 无截图记录")
        return []

    valid = [(p, t) for p, t in rows if Path(p).exists()]
    if not valid:
        logger.warning(f"{date_str} 截图文件均不存在（可能已清理）")
        return []

    # ── 2. 按显示器分组（各显示器独立时间序列）
    from collections import defaultdict
    monitors: Dict[str, List[tuple]] = defaultdict(list)
    for path, ts in valid:
        mid = _extract_monitor_id(path)
        monitors[mid].append((path, ts))

    logger.info(f"检测到 {len(monitors)} 个显示器：{list(monitors.keys())}")

    # ── 3. 每个显示器独立计算 dHash 变化量
    #    per_monitor_scores: { monitor_id: [(path, ts, importance), ...] }
    per_monitor_scores: Dict[str, List[tuple]] = {}
    for mid, frames in monitors.items():
        hashes = []
        for path, _ in frames:
            try:
                img = Image.open(path)
                hashes.append(_image_hash(img))
            except Exception:
                hashes.append("")

        scores = [64]  # 第一帧默认高重要性（一天的开始）
        for i in range(1, len(frames)):
            h1, h2 = hashes[i - 1], hashes[i]
            scores.append(_hamming_distance(h1, h2) if (h1 and h2) else 0)

        per_monitor_scores[mid] = [
            (path, ts, score) for (path, ts), score in zip(frames, scores)
        ]

    # ── 4. 构建「时间组」：同一秒内的不同显示器截图归为一组
    #    以时间戳前16位（分钟级）对齐，兼容1s内的微小时差
    from collections import OrderedDict
    time_groups: Dict[str, Dict[str, tuple]] = OrderedDict()  # ts_key → {mid: (path, ts, score)}

    for mid, frames in per_monitor_scores.items():
        for path, ts, score in frames:
            # 用截图时间的前19位（秒级）作为组key（同一次触发的两个显示器时间相同）
            ts_key = ts[:19]
            if ts_key not in time_groups:
                time_groups[ts_key] = {}
            time_groups[ts_key][mid] = (path, ts, score)

    # ── 5. 每组的「重要性」= 组内各显示器变化量的最大值
    group_importance = [
        (ts_key, max(v[2] for v in screens.values()), screens)
        for ts_key, screens in time_groups.items()
    ]

    total_groups = len(group_importance)
    if total_groups <= top_n:
        # 组数不超过 top_n，全部返回
        selected_groups = group_importance
    else:
        # 按重要性降序选 top_n 组
        selected_groups = sorted(group_importance, key=lambda x: x[1], reverse=True)[:top_n]
        # 按时间顺序重排
        ts_order = {ts_key: i for i, (ts_key, _, _) in enumerate(group_importance)}
        selected_groups = sorted(selected_groups, key=lambda x: ts_order[x[0]])

    # ── 6. 展开为截图列表（双屏每组产生2张）
    result = []
    for ts_key, importance, screens in selected_groups:
        for mid in sorted(screens.keys()):   # monitor_1 在前，monitor_2 在后
            path, ts, _ = screens[mid]
            result.append({
                "path": path,
                "captured_at": ts,
                "importance": importance,
                "monitor": mid,
            })

    logger.info(
        f"{date_str} 共 {len(valid)} 张截图 / {total_groups} 个时间组，"
        f"选出 {len(selected_groups)} 组（{len(result)} 张，含 {len(monitors)} 个显示器）"
    )
    return result


def screenshots_to_context(
    shots: List[Dict],
    prompt: str = "请描述这张截图中用户正在做什么，用一句话概括，不超过50字。",
) -> List[Dict]:
    """
    将选出的截图批量发给 Vision LLM，返回带描述的列表。
    Day 3 日报生成时：把描述列表拼成上下文 prompt。

    Returns:
        List[Dict]: [{"captured_at": str, "description": str}, ...]
    """
    from llm_client import vision_chat
    from utils.image_compress import compress_for_llm

    results = []
    for shot in shots:
        path = Path(shot["path"])
        b64 = compress_for_llm(path)
        if b64 is None:
            continue
        desc = vision_chat(image_b64=b64, prompt=prompt)
        if desc:
            results.append({
                "captured_at": shot["captured_at"],
                "description": desc,
            })
            logger.debug(f"{shot['captured_at'][11:16]}: {desc[:40]}")

    return results


def build_screenshot_context_text(date_str: str, screenshot_dir: str, db_path: str, top_n: int = 10) -> str:
    """
    一步到位：选图 → Vision 分析 → 拼成自然语言上下文
    Day 3 日报 prompt 直接调用这个。

    Returns:
        str: 给 LLM 的截图上下文段落
    """
    shots = select_representative(date_str, screenshot_dir, db_path, top_n)
    if not shots:
        return "（今日无截图记录）"

    described = screenshots_to_context(shots)
    if not described:
        return "（截图分析失败）"

    lines = [f"今日 {len(described)} 张关键截图时间线："]
    for item in described:
        t = item["captured_at"][11:16]
        lines.append(f"  {t}  {item['description']}")

    return "\n".join(lines)
