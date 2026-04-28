"""
LLM Activity Classifier — 批量 LLM 分类器

设计参考：
  - Screenpipe workflow_classifier.rs: hash 去重 + 置信度过滤 + 冷却机制
  - Windrecorder llm.py: 日报前批量调用 + 结果缓存到本地
  - ActivityWatch classes.ts: 层级规则优先 + Uncategorized 兜底

核心流程：
  1. 查询当天所有 classification_source='pending' 的活动记录
  2. 按 (process_name, window_title 前 40 字) 去重聚合
  3. 一次 LLM chat() 调用批量分类
  4. 结果写回 window_activities 表
  5. 自动缓存到 app_rules 表（下次不再问 LLM）

Token 预算：~800-1500 token/天，约 ¥0.01，可忽略不计
"""
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from llm_constants import (
    CLASSIFIER_BASE_MAX_TOKENS,
    CLASSIFIER_PER_ACTIVITY_TOKENS,
    CLASSIFIER_SCREENSHOT_MAX_TOKENS,
)

logger = logging.getLogger("navi.classifier")

# ─────────────────────────────────────────────
# 分类 Prompt（参考 Screenpipe CLASSIFIER_SYSTEM_PROMPT 设计）
# ─────────────────────────────────────────────

CLASSIFIER_SYSTEM_PROMPT = """你是一个桌面活动分类器。根据用户提供的应用程序名称和窗口标题，判断每项活动属于以下哪个类别：

- work: 编码、写文档、设计、项目管理、会议、邮件等工作相关活动
- learning: 阅读教程、看技术课程、做笔记、查文档等学习相关活动
- entertainment: 视频、游戏、社交媒体、音乐、直播等娱乐相关活动
- communication: 即时通讯（微信、钉钉、Slack 等），根据上下文可能是工作也可能是社交
- utility: 文件管理器、系统设置、计算器等中性系统工具

分类规则：
1. 浏览器活动需根据窗口标题（网页标题）判断，而不是根据浏览器本身
2. 同一个进程可能因窗口标题不同而归属不同类别
3. 不确定时，倾向标记为 work（工作活动通常比娱乐活动更多样化）
4. 用中文思考，但 category 值必须用英文

请严格按 JSON 数组格式返回，每项包含 id、category 和 confidence（0.0~1.0 的置信度）：
[{"id": 1, "category": "work", "confidence": 0.95}, {"id": 2, "category": "entertainment", "confidence": 0.6}, ...]

置信度说明：
- 0.9+：非常确定（如 cursor.exe 明显是编码工作）
- 0.7~0.9：比较确定
- < 0.7：不太确定（如窗口标题模糊、进程名不认识）

只输出 JSON，不要任何其他说明。"""

# 有效分类值
VALID_CATEGORIES = {"work", "learning", "entertainment", "communication", "utility"}


def classify_pending_activities(db_path: str, date_str: Optional[str] = None) -> int:
    """
    对指定日期中 classification_source='pending' 的活动记录做 LLM 批量分类。
    
    Args:
        db_path: SQLite 数据库路径
        date_str: 日期字符串 'YYYY-MM-DD'，默认今天
    
    Returns:
        本次分类更新的记录数
    """
    from datetime import date as date_cls
    date_str = date_str or date_cls.today().isoformat()

    # ── Step 1: 查询所有 pending 记录 ──────────────────────────────────
    pending = _get_pending_activities(db_path, date_str)
    if not pending:
        logger.info(f"[LLM分类] {date_str} 无 pending 记录，跳过")
        return 0

    logger.info(f"[LLM分类] {date_str} 共 {len(pending)} 条 pending 记录")

    # ── Step 2: 按 (process_name, title_prefix) 去重聚合 ───────────────
    unique_activities, id_mapping = _deduplicate_activities(pending)
    logger.info(f"[LLM分类] 去重后 {len(unique_activities)} 种 unique 活动")

    if not unique_activities:
        return 0

    # ── Step 3: Level 1 — 纯文本 LLM 批量分类 ─────────────────────────
    classifications, fuzzy_uids = _call_llm_classify(unique_activities)
    if not classifications:
        logger.warning("[LLM分类] LLM 调用失败或返回为空")
        return 0

    logger.info(f"[LLM分类] Level 1 返回 {len(classifications)} 条，其中 {len(fuzzy_uids)} 条模糊")

    # ── Step 3.5: Level 2 — 截图辅助分类（仅对高时长+低置信度项）──────
    if fuzzy_uids:
        # 只对累计时长 > 5 分钟的模糊项启用 Vision，且最多 3 张/天
        uid_to_act = {act["uid"]: act for act in unique_activities}
        worth_vision = [
            uid for uid in fuzzy_uids
            if uid_to_act.get(uid, {}).get("total_sec", 0) >= 300  # ≥5 分钟
        ]
        # 按时长降序，只取前 3 个（日预算上限）
        worth_vision.sort(key=lambda u: uid_to_act.get(u, {}).get("total_sec", 0), reverse=True)
        worth_vision = worth_vision[:3]

        if worth_vision:
            logger.info(
                f"[LLM分类] {len(fuzzy_uids)} 条模糊项中，"
                f"{len(worth_vision)} 条时长≥5min，启用 Vision 辅助"
            )
            vision_updates = _vision_classify_fuzzy(
                db_path, date_str, worth_vision, unique_activities, id_mapping
            )
            for uid, cat in vision_updates.items():
                classifications[uid] = cat
            logger.info(f"[LLM分类] Level 2 Vision 修正了 {len(vision_updates)} 条分类")
        else:
            logger.info(f"[LLM分类] {len(fuzzy_uids)} 条模糊项均 <5min，跳过 Vision")

    # ── Step 4: 写回 window_activities 表 ──────────────────────────────
    updated = _update_activities(db_path, classifications, id_mapping)

    # ── Step 5: 缓存到 app_rules 表 ───────────────────────────────────
    _cache_to_app_rules(db_path, classifications, unique_activities)

    logger.info(f"[LLM分类] ✅ 完成：{updated} 条记录已更新分类")
    return updated


# ─────────────────────────────────────────────
# 内部函数
# ─────────────────────────────────────────────

def _get_pending_activities(db_path: str, date_str: str) -> List[Dict]:
    """查询指定日期中 classification_source='pending' 的记录"""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, process_name, window_title, duration_sec
            FROM window_activities
            WHERE date(started_at) = ?
              AND classification_source = 'pending'
              AND is_synced != -1
            ORDER BY started_at ASC
        """, (date_str,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[LLM分类] 查询 pending 失败: {e}")
        return []


def _deduplicate_activities(
    pending: List[Dict],
) -> Tuple[List[Dict], Dict[int, List[int]]]:
    """
    按 (process_name_lower, window_title 前 40 字) 去重聚合。
    
    参考 Screenpipe 的 hash_activities() 思路：
    相同进程+标题前缀的记录只发送一次给 LLM，减少 token 消耗。
    
    Returns:
        unique_activities: [{"uid": 1, "process": ..., "title": ..., "total_sec": ...}, ...]
        id_mapping: {uid: [db_row_id, db_row_id, ...]}  — uid 对应的所有原始记录 ID
    """
    groups: Dict[str, Dict] = {}  # key → group info
    id_mapping: Dict[int, List[int]] = {}  # uid → [row_ids]

    for row in pending:
        proc = row["process_name"].lower()
        title_prefix = (row["window_title"] or "")[:40].strip()
        key = f"{proc}|||{title_prefix}"

        if key not in groups:
            uid = len(groups) + 1
            groups[key] = {
                "uid": uid,
                "process": row["process_name"],
                "title": row["window_title"] or "",
                "total_sec": row.get("duration_sec", 0) or 0,
            }
            id_mapping[uid] = [row["id"]]
        else:
            groups[key]["total_sec"] += row.get("duration_sec", 0) or 0
            uid = groups[key]["uid"]
            id_mapping[uid].append(row["id"])

    unique = sorted(groups.values(), key=lambda x: x["uid"])
    return unique, id_mapping


def _call_llm_classify(unique_activities: List[Dict]) -> Tuple[Dict[int, str], List[int]]:
    """
    一次 LLM 调用批量分类所有 unique 活动。
    
    参考 Screenpipe 的 classify() 函数：
    - temperature=0.1 保证输出稳定
    - max_tokens 按活动数量动态计算
    - 解析 JSON 结果，过滤无效分类
    
    Returns:
        (classifications, fuzzy_uids)
        - classifications: {uid: category}
        - fuzzy_uids: 置信度 < 0.7 的 uid 列表
    """
    # 构建 prompt
    lines = []
    for act in unique_activities:
        dur_min = max(1, act["total_sec"] // 60)
        # 只取标题前 60 字，避免 prompt 太长
        title_short = act["title"][:60]
        lines.append(f'{act["uid"]}. process="{act["process"]}", title="{title_short}", duration={dur_min}min')

    user_prompt = "请对以下桌面活动进行分类：\n\n" + "\n".join(lines)

    # 调用 LLM
    try:
        from llm_client import chat
        raw = chat(
            messages=[{"role": "user", "content": user_prompt}],
            system=CLASSIFIER_SYSTEM_PROMPT,
            temperature=0.1,  # 参考 Screenpipe: temperature=0.1 保证稳定
            max_tokens=max(CLASSIFIER_BASE_MAX_TOKENS, len(unique_activities) * CLASSIFIER_PER_ACTIVITY_TOKENS),
        )
    except Exception as e:
        logger.error(f"[LLM分类] chat() 调用失败: {e}")
        return {}, []

    if not raw:
        return {}, []

    logger.debug(f"[LLM分类] 原始返回: {raw[:500]}")

    # 解析 JSON（兼容 markdown code block 包裹）
    return _parse_classification_response(raw)


def _parse_classification_response(raw: str) -> Tuple[Dict[int, str], List[int]]:
    """
    解析 LLM 返回的分类 JSON，同时提取低置信度项。
    
    兼容场景：
    - 纯 JSON 数组
    - ```json ... ``` 代码块包裹
    - 带额外说明文字（只提取 JSON 部分）
    
    Returns:
        (classifications, fuzzy_uids)
        - classifications: {uid: category}
        - fuzzy_uids: 置信度 < 0.7 的 uid 列表，需要截图辅助
    """
    results: Dict[int, str] = {}
    fuzzy_uids: List[int] = []

    # 尝试提取 JSON 数组
    # 先试 markdown code block
    code_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', raw)
    if code_match:
        json_str = code_match.group(1)
    else:
        # 直接找 [...] 
        arr_match = re.search(r'\[[\s\S]*\]', raw)
        if arr_match:
            json_str = arr_match.group(0)
        else:
            logger.warning(f"[LLM分类] 无法从返回中提取 JSON: {raw[:200]}")
            return results, fuzzy_uids

    try:
        items = json.loads(json_str)
        for item in items:
            uid = item.get("id")
            cat = item.get("category", "").lower().strip()
            confidence = float(item.get("confidence", 1.0))
            if uid is not None and cat in VALID_CATEGORIES:
                results[int(uid)] = cat
                if confidence < 0.7:
                    fuzzy_uids.append(int(uid))
                    logger.info(f"[LLM分类] id={uid} '{cat}' 置信度={confidence:.2f} → 标记为模糊项")
            elif uid is not None:
                logger.debug(f"[LLM分类] 无效分类值 id={uid} category='{cat}'，跳过")
    except json.JSONDecodeError as e:
        logger.warning(f"[LLM分类] JSON 解析失败: {e}, 原文: {json_str[:200]}")

    return results, fuzzy_uids


def _update_activities(
    db_path: str,
    classifications: Dict[int, str],
    id_mapping: Dict[int, List[int]],
) -> int:
    """
    把 LLM 分类结果写回 window_activities 表。
    
    参考 Windrecorder 的缓存写入模式：批量 UPDATE，一次事务。
    """
    updated = 0
    try:
        conn = sqlite3.connect(db_path)
        with conn:
            for uid, category in classifications.items():
                row_ids = id_mapping.get(uid, [])
                if not row_ids:
                    continue
                placeholders = ",".join("?" * len(row_ids))
                conn.execute(
                    f"UPDATE window_activities "
                    f"SET app_category = ?, classification_source = 'llm' "
                    f"WHERE id IN ({placeholders})",
                    [category] + row_ids,
                )
                updated += len(row_ids)
        conn.close()
    except Exception as e:
        logger.error(f"[LLM分类] 写回 DB 失败: {e}")

    return updated


def _cache_to_app_rules(
    db_path: str,
    classifications: Dict[int, str],
    unique_activities: List[Dict],
) -> None:
    """
    把 LLM 分类结果缓存到 app_rules 表。
    
    参考 ActivityWatch 的 category rule 缓存设计：
    - LLM 结果写入时 is_user_defined=0，不覆盖用户手动规则
    - 下次相同 process_name 直接命中缓存，不再调 LLM
    
    注意：浏览器类进程不缓存到 app_rules（因为同一浏览器打开不同网页类别不同）
    """
    browser_processes = {"chrome.exe", "msedge.exe", "firefox.exe", "browser_broker.exe"}

    try:
        conn = sqlite3.connect(db_path)
        now = datetime.now().isoformat()
        with conn:
            for act in unique_activities:
                uid = act["uid"]
                category = classifications.get(uid)
                if not category:
                    continue

                proc_lower = act["process"].lower()
                # 浏览器不缓存进程级规则（标题决定分类）
                if proc_lower in browser_processes:
                    continue

                # UPSERT: 只在 is_user_defined=0 时更新（不覆盖用户手动规则）
                conn.execute("""
                    INSERT INTO app_rules (process_name, category, is_user_defined, auto_tagged_at)
                    VALUES (?, ?, 0, ?)
                    ON CONFLICT(process_name) DO UPDATE SET
                        category = excluded.category,
                        auto_tagged_at = excluded.auto_tagged_at
                    WHERE is_user_defined = 0
                """, (proc_lower, category, now))

        conn.close()
        logger.info(f"[LLM分类] app_rules 缓存已更新")
    except Exception as e:
        logger.warning(f"[LLM分类] 缓存到 app_rules 失败: {e}")


# ─────────────────────────────────────────────
# Level 2: 截图辅助分类（低置信度项）
# ─────────────────────────────────────────────

VISION_CLASSIFY_PROMPT = """根据这张电脑截图，判断用户正在进行的活动属于以下哪个类别：
- work: 编码、写文档、设计、项目管理、会议、邮件等工作
- learning: 阅读教程、看课程、做笔记等学习
- entertainment: 视频、游戏、社交媒体、音乐等娱乐
- communication: 即时通讯、邮件等沟通
- utility: 文件管理器、系统设置等工具

只输出一个 JSON：{"category": "xxx"}
不要任何其他说明。"""


def _vision_classify_fuzzy(
    db_path: str,
    date_str: str,
    fuzzy_uids: List[int],
    unique_activities: List[Dict],
    id_mapping: Dict[int, List[int]],
) -> Dict[int, str]:
    """
    Level 2: 对 Level 1 低置信度项，通过时间戳匹配最近的截图，
    调用 Vision API 辅助分类。
    
    通过 window_activities.started_at ~ ended_at 时间段，
    匹配 screenshots.captured_at 找到对应截图。
    
    每个模糊项只取 1 张截图（最接近活动中间时间点的），控制 Vision 成本。
    
    Returns:
        {uid: category} — 仅包含 Vision 成功修正的项
    """
    results: Dict[int, str] = {}

    # 构建 uid → activity 映射
    uid_to_act = {act["uid"]: act for act in unique_activities}

    # 取模糊项的原始 row_ids，查询它们的时间段
    fuzzy_row_ids = []
    uid_for_row: Dict[int, int] = {}  # row_id → uid
    for uid in fuzzy_uids:
        row_ids = id_mapping.get(uid, [])
        for rid in row_ids[:1]:  # 每个 uid 只取第一条记录的时间段
            fuzzy_row_ids.append(rid)
            uid_for_row[rid] = uid

    if not fuzzy_row_ids:
        return results

    # 查询活动的时间范围 + 匹配截图
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        for row_id in fuzzy_row_ids:
            uid = uid_for_row[row_id]
            act = uid_to_act.get(uid)
            if not act:
                continue

            # 查这条活动的时间范围
            wa = conn.execute(
                "SELECT started_at, ended_at FROM window_activities WHERE id = ?",
                (row_id,)
            ).fetchone()
            if not wa or not wa["started_at"]:
                continue

            started = wa["started_at"]
            ended = wa["ended_at"] or started

            # 找这个时间段内或前后 2 分钟内最近的截图
            # 策略 1: 精确匹配时间段内的截图（含 duplicate，因为画面没变也能判断类别）
            shot = conn.execute("""
                SELECT file_path, captured_at FROM screenshots
                WHERE captured_at BETWEEN ? AND ?
                ORDER BY ABS(
                    julianday(captured_at) - (julianday(?) + julianday(?)) / 2
                ) ASC
                LIMIT 1
            """, (started, ended, started, ended)).fetchone()

            # 策略 2: 时间段内没找到 → 向前后各扩展 2 分钟
            if not shot or not shot["file_path"]:
                shot = conn.execute("""
                    SELECT file_path, captured_at FROM screenshots
                    WHERE captured_at BETWEEN datetime(?, '-2 minutes') AND datetime(?, '+2 minutes')
                    ORDER BY ABS(julianday(captured_at) - julianday(?)) ASC
                    LIMIT 1
                """, (started, ended, started)).fetchone()

            # 策略 3: 还是没有 → 放弃，用 Level 1 结果兜底
            if not shot or not shot["file_path"]:
                logger.debug(f"[Vision分类] uid={uid} 无匹配截图（含±2min扩展），保留 Level 1 结果")
                continue

            screenshot_path = shot["file_path"]
            if not Path(screenshot_path).exists():
                logger.debug(f"[Vision分类] 截图文件不存在: {screenshot_path}")
                continue

            # 调用 Vision API
            category = _vision_single_classify(screenshot_path, act)
            if category:
                results[uid] = category
                logger.info(
                    f"[Vision分类] uid={uid} '{act['process']}' "
                    f"→ {category}（截图: {Path(screenshot_path).name}）"
                )

        conn.close()
    except Exception as e:
        logger.error(f"[Vision分类] 查询失败: {e}")

    return results


def _vision_single_classify(screenshot_path: str, activity: Dict) -> Optional[str]:
    """
    对单张截图调用 Vision API 判断活动类别。
    
    Args:
        screenshot_path: 截图文件路径
        activity: unique_activity 字典
    
    Returns:
        category 字符串，失败返回 None
    """
    try:
        from utils.image_compress import compress_for_llm
        from llm_client import vision_chat

        b64 = compress_for_llm(Path(screenshot_path), max_side=512, quality=60)
        if not b64:
            return None

        prompt = (
            f"当前应用: {activity['process']}，窗口标题: {activity['title'][:50]}\n\n"
            f"{VISION_CLASSIFY_PROMPT}"
        )

        raw = vision_chat(b64, prompt, max_tokens=CLASSIFIER_SCREENSHOT_MAX_TOKENS)
        if not raw:
            return None

        # 解析 JSON
        m = re.search(r'\{[\s\S]*?\}', raw)
        if m:
            data = json.loads(m.group(0))
            cat = data.get("category", "").lower().strip()
            if cat in VALID_CATEGORIES:
                return cat

        # fallback: 直接在文本里找类别关键词
        for cat in VALID_CATEGORIES:
            if cat in raw.lower():
                return cat

    except Exception as e:
        logger.warning(f"[Vision分类] 调用失败: {e}")

    return None
