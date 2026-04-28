"""
report_generator.py - 日报生成器（Day 3 核心）

流程：
  1. 读今日窗口活动（SQLite）
  2. 【P0-2 新增】Gap 检测：用 LLM 找出值得追问的活动段，暂停日报推送学习问题
     ├─ 用户回答（≤10 分钟）→ 用 learning_summary 重新触发
     └─ 超时 → skip_gap_check=True 强制生成
  3. select_representative() 选30张截图 → 3批并行 Vision（每批10张）→ 截图时间线
  4. episodic_memory.search() 检索相关历史记忆
  5. 一次 chat() 生成日报 Markdown（system prompt 注入 SOUL.md 人格）
  6. 写入 Obsidian 目录 + daily_reports 表 + EpisodicMemory

Token 预算（¥0.5/天）：
  - Gap 检测：~500 token ≈ ¥0.001
  - LLM 活动分类：约 1,500 token（Level 1 文本）+ ≤15,000（Level 2 Vision）
  - Vision 20张截图描述：约 30,000 token（4批并行，每批5张）
  - 窗口活动文字：约 2,000 token
  - 日报生成：约 10,000 token（含模型思考链）
  - 合计：约 60,000 token ≈ ¥0.3，留有余量
"""
import sqlite3
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger("navi.report")

# ─────────────────────────────────────────────
# 配置常量（从 llm_constants.py 统一读取）
# ─────────────────────────────────────────────
from llm_constants import (
    REPORT_TOP_N_SCREENSHOTS, REPORT_VISION_BATCH_SIZE,
    REPORT_MAX_DESC_LEN, REPORT_MAX_TOKENS,
    REPORT_VISION_DESC_BASE_TOKENS,
)

TOP_N_SCREENSHOTS = REPORT_TOP_N_SCREENSHOTS
BATCH_SIZE        = REPORT_VISION_BATCH_SIZE
MAX_DESC_LEN      = REPORT_MAX_DESC_LEN


# ────────────────────────────────────────────
# 1. 数据采集层
# ────────────────────────────────────────────

def _get_activities(db_path: str, date_str: str) -> List[Dict]:
    """
    从 SQLite 读取今日活动（支持父子层级）。
    只返回大段（parent_id IS NULL），每个大段包含 children 子活动列表。
    大段的 duration_sec 已经是所有子活动时长之和。
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # 查询大段
        parent_rows = conn.execute("""
            SELECT id, process_name, window_title, started_at, ended_at,
                   duration_sec, app_category
            FROM window_activities
            WHERE date(started_at) = ? AND parent_id IS NULL AND is_synced != -1
            ORDER BY started_at ASC
        """, (date_str,)).fetchall()

        # 查询子活动
        child_rows = conn.execute("""
            SELECT process_name, window_title, started_at, ended_at,
                   duration_sec, app_category, parent_id
            FROM window_activities
            WHERE date(started_at) = ? AND parent_id IS NOT NULL
            ORDER BY started_at ASC
        """, (date_str,)).fetchall()
        conn.close()

        # 按 parent_id 分组
        children_map: Dict[int, List[Dict]] = {}
        for c in child_rows:
            cd = dict(c)
            pid = cd.pop("parent_id")
            children_map.setdefault(pid, []).append(cd)

        result = []
        for r in parent_rows:
            d = dict(r)
            row_id = d.pop("id", None)
            d["children"] = children_map.get(row_id, [])
            result.append(d)

        return result
    except Exception as e:
        logger.error(f"读取活动记录失败：{e}")
        return []


def _merge_children(children: List[Dict]) -> List[Dict]:
    """把子活动按 process_name 合并，累加时长（与前端 mergeChildren 逻辑一致）"""
    merged: Dict[str, Dict] = {}
    for c in children:
        key = c.get("process_name", "").lower()
        if key in merged:
            merged[key]["duration_sec"] += c.get("duration_sec", 0) or 0
            merged[key]["window_title"] = c.get("window_title", "")  # 保留最新标题
        else:
            merged[key] = {
                "process_name": c.get("process_name", ""),
                "window_title": c.get("window_title", ""),
                "app_category": c.get("app_category", "other"),
                "duration_sec": c.get("duration_sec", 0) or 0,
            }
    return sorted(merged.values(), key=lambda x: x["duration_sec"], reverse=True)


def _fmt_dur(sec: int) -> str:
    """秒 → 可读时长"""
    if sec >= 3600:
        return f"{sec // 3600}h{(sec % 3600) // 60}m"
    elif sec >= 60:
        return f"{sec // 60}m"
    return f"{sec}s"


def _activities_to_text(activities: List[Dict]) -> str:
    """
    活动记录 → 精简文字（发给 LLM 生成日报）。
    大段按分类聚合，每个大段下展示合并后的子活动明细。
    """
    if not activities:
        return "今日无活动记录。"

    cat_names = {"work": "工作", "learning": "学习", "entertainment": "娱乐",
                 "communication": "沟通", "utility": "工具", "other": "其他"}

    lines = ["【今日活动摘要】"]
    total_sec = sum(a.get("duration_sec", 0) or 0 for a in activities)
    lines.append(f"总时长：{total_sec // 3600}小时{(total_sec % 3600) // 60}分钟，共 {len(activities)} 个活动段")
    lines.append("")

    # 按时间顺序逐个大段输出
    for a in activities:
        started = (a.get("started_at") or "")[:16].replace("T", " ")
        dur = a.get("duration_sec", 0) or 0
        cat = a.get("app_category", "other")
        label = cat_names.get(cat, cat)
        proc = a.get("process_name", "").replace(".exe", "")
        title = (a.get("window_title") or "")[:50]

        lines.append(f"▸ {started} [{label}] {proc} — {title}（{_fmt_dur(dur)}）")

        # 子活动明细（合并后）
        children = a.get("children", [])
        if children:
            merged = _merge_children(children)
            for mc in merged:
                mc_proc = mc["process_name"].replace(".exe", "")
                mc_cat = cat_names.get(mc["app_category"], mc["app_category"])
                mc_title = (mc["window_title"] or "")[:40]
                mc_dur = mc["duration_sec"]
                lines.append(f"    · {mc_proc}[{mc_cat}] {mc_title}（{_fmt_dur(mc_dur)}）")

    return "\n".join(lines)


# ────────────────────────────────────────────
# 2. 截图处理层（并行 Vision，¥0.4/天预算）
# ────────────────────────────────────────────

def _get_screenshot_timeline(
    db_path: str,
    screenshot_dir: str,
    date_str: str,
    top_n: int = TOP_N_SCREENSHOTS,
    batch_size: int = BATCH_SIZE,
) -> str:
    """
    选最有变化的 top_n 张截图 → 并行 Vision 调用（每批 batch_size 张）
    返回截图时间线文字
    """
    from utils.screenshot_selector import select_representative

    shots = select_representative(date_str, screenshot_dir, db_path, top_n)
    if not shots:
        return "（今日无截图记录）"

    logger.info(f"选出 {len(shots)} 张代表截图，开始并行 Vision 分析")

    # 检查 DB 缓存（同一天重新生成日报时不重复调用）
    described = _get_cached_descriptions(db_path, [s["path"] for s in shots])
    uncached = [s for s in shots if s["path"] not in described]
    logger.info(f"缓存命中 {len(described)} 张，需分析 {len(uncached)} 张")

    if uncached:
        # 分批
        batches = [uncached[i: i + batch_size] for i in range(0, len(uncached), batch_size)]
        logger.info(f"分 {len(batches)} 批并行 Vision 调用（每批最多 {batch_size} 张）")

        # 并行执行
        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            futures = {executor.submit(_analyze_batch, batch, db_path): batch for batch in batches}
            for future in as_completed(futures):
                batch_result = future.result()  # dict: path -> desc
                described.update(batch_result)

    # 拼时间线
    lines = [f"【截图时间线（{len(shots)} 张关键截图）】"]
    for s in shots:
        t = s["captured_at"][11:16]
        desc = described.get(s["path"], "（分析失败）")
        lines.append(f"  {t}  {desc}")

    return "\n".join(lines)


def _get_cached_descriptions(db_path: str, paths: List[str]) -> Dict[str, str]:
    """从 DB 读取已分析过的截图描述"""
    try:
        conn = sqlite3.connect(db_path)
        result = {}
        for path in paths:
            row = conn.execute(
                "SELECT llm_description FROM screenshots WHERE file_path=? AND llm_description IS NOT NULL",
                (path,)
            ).fetchone()
            if row and row[0]:
                result[path] = row[0]
        conn.close()
        return result
    except Exception:
        return {}


def _analyze_batch(shots: List[Dict], db_path: str) -> Dict[str, str]:
    """
    一次 Vision 调用分析一批截图。
    使用 llm_client.vision_batch() 复用统一接口，避免重复配置。
    返回 {path: description} dict，并写入 DB 缓存。
    """
    from utils.image_compress import compress_for_llm
    import re

    # 压缩图片（用 512px 而非默认 1024px，减少每批请求体积避免服务端截断）
    content = []
    valid_shots = []
    for s in shots:
        b64 = compress_for_llm(Path(s["path"]), max_side=512, quality=60)
        if b64:
            content.append({"b64": b64, "shot": s})
            valid_shots.append(s)

    if not valid_shots:
        logger.warning("_analyze_batch: 所有截图压缩失败，跳过本批次")
        return {}

    times = [s["captured_at"][11:16] for s in valid_shots]
    prompt_text = (
        f"这是{len(valid_shots)}张按时间顺序排列的电脑截图，"
        f"时间分别是：{', '.join(times)}。\n"
        f"请按顺序，每张截图用一句话（不超过{MAX_DESC_LEN}字）描述用户在做什么。\n"
        f"严格按如下格式输出，每行一条，不要多余说明：\n"
        + "\n".join(f"{t}：[描述]" for t in times)
    )

    # 用 llm_client.vision_batch 统一调用
    from llm_client import vision_batch
    raw = vision_batch([c["b64"] for c in content], prompt_text,
                       max_tokens=max(600, len(valid_shots) * (MAX_DESC_LEN + 20)))
    logger.info(f"Vision 批次（{len(valid_shots)}张）返回：{raw[:150] if raw else '空'}")

    result: Dict[str, str] = {}
    if raw:
        for i, s in enumerate(valid_shots):
            t = s["captured_at"][11:16]
            m = re.search(rf"{t}[：:]\s*(.+)", raw)
            if m:
                desc = m.group(1).strip()
            else:
                lines = [l.strip() for l in raw.split("\n") if l.strip()]
                desc = lines[i] if i < len(lines) else "用户在使用电脑"
            result[s["path"]] = desc
    else:
        logger.error("Vision 批次返回为空，请检查 API Key 和模型配置")
        for s in valid_shots:
            result[s["path"]] = "（图片分析失败）"
        return result  # 不写 DB

    # 写入 DB 缓存
    try:
        conn = sqlite3.connect(db_path)
        for s in valid_shots:
            desc = result.get(s["path"], "")
            if desc and "分析失败" not in desc:
                conn.execute(
                    "UPDATE screenshots SET llm_description=?, sent_to_llm=1 WHERE file_path=?",
                    (desc, s["path"])
                )
        conn.commit()
        conn.close()
        logger.info(f"写入 DB 缓存：{len([v for v in result.values() if '分析失败' not in v])} 条")
    except Exception as e:
        logger.warning(f"描述写入 DB 失败：{e}")

    return result


# ────────────────────────────────────────────
# 3. 记忆检索层
# ────────────────────────────────────────────

def _get_memory_context(activities_text: str, date_str: str) -> str:
    """从 EpisodicMemory 检索相关历史，提供对比和参照"""
    try:
        from memory.episodic_memory import get_episodic_memory
        from config import get_config
        em = get_episodic_memory(get_config().memory_dir)
        if em.count() == 0:
            return "（暂无历史记忆）"

        results = em.search(activities_text[:200], n=5)
        if not results:
            return "（未找到相关历史）"

        lines = ["【历史参照（最相关的过往记录）】"]
        for r in results:
            if r["date"] != date_str:
                lines.append(f"  {r['date']}  {r['text'][:100]}")

        return "\n".join(lines) if len(lines) > 1 else "（暂无相关历史）"
    except Exception as e:
        logger.warning(f"记忆检索失败：{e}")
        return "（记忆检索失败）"


# ────────────────────────────────────────────
# 4. 日报生成层
# ────────────────────────────────────────────

# 日报模板规则（追加在 SOUL.md 之后，限定输出格式）
_REPORT_TASK_RULES = """---

## 当前任务：生成今日日报

根据用户今天的活动数据，生成一份简洁有温度的日报。

【格式硬约束 — 这是写文档不是聊天，违反会让日报损坏】
- 输出**完整 Markdown 文档**，不是即时聊天消息
- **不要使用 [SPLIT] 分段标记**（那是聊天才用的）
- **不要用括号包裹的动作/神态描写**（如「（歪头）」「（眨眼）」）
- 不要在文档里使用过多语气词、口癖
- 保留你的视角、用词倾向、价值观，但呈现为一份正式日报
- 称呼用户用"用户"或直接用第二人称叙述（不要硬塞角色专属称呼）

【内容要求】
- 风格：客观记录 + 你的视角点评，像一个了解用户的朋友在总结他的一天
- 不要浮夸，不要教导式语气
- 结构固定（见下方模板），不要加多余章节

【模板 — 严格遵守】
# {date} 日报

## 今日概览
（1-2 句话概括今天最重要的事）

## 活动时间线
（按时间顺序，重要节点的流水账，结合截图描述和窗口记录，控制在 200 字内）

## 今日数据
- 有效工作时间：xxx
- 主要使用工具：xxx
- 专注时段：xxx

## 我的观察
（结合历史记忆和你对用户的了解，1-3 条有洞察的观察，例如「这周学习密度比上周高了不少」「今天下午明显更专注」）

## 明日建议
（1-2 条可执行的建议，基于今天的情况）
"""

# 默认身份（SOUL.md 不存在时的兜底）
_DEFAULT_IDENTITY = "你是 Navi，用户的个人 AI 助手。"


def _build_system_prompt() -> str:
    """
    构建日报 system prompt：SOUL.md 人格 + 日报模板规则。

    优先读 backend/templates/SOUL.md（vault 写入位置），保持和 agent context.py 一致。
    SOUL 不存在时降级为默认身份。
    """
    soul_path = Path(__file__).parent.parent / "templates" / "SOUL.md"
    if soul_path.exists():
        try:
            soul_content = soul_path.read_text(encoding="utf-8").strip()
            if soul_content:
                return f"{soul_content}\n{_REPORT_TASK_RULES}"
        except Exception as e:
            logger.warning(f"读取 SOUL.md 失败，降级为默认身份: {e}")
    return f"{_DEFAULT_IDENTITY}\n{_REPORT_TASK_RULES}"


def _build_prompt(
    activities_text: str,
    screenshot_timeline: str,
    memory_context: str,
    date_str: str,
    learning_summary: Optional[str] = None,
) -> str:
    """构建用户 prompt。learning_summary 是用户回答的"今日学习收获"自述。"""
    learning_section = ""
    if learning_summary and learning_summary.strip():
        learning_section = f"""

【用户今日自述（关于学习/工作内容）】
{learning_summary.strip()}

请在「今日概览」和「我的观察」中适当引用用户的自述，让日报更贴近用户的实际感受。"""

    return f"""今天是 {date_str}，以下是用户今天的完整数据：

{activities_text}

{screenshot_timeline}

{memory_context}{learning_section}

请根据以上数据生成今日日报。"""


def _write_to_obsidian(content: str, date_str: str, output_dir: str) -> str:
    """写入 Obsidian 目录，返回文件路径"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    filename = f"{date_str}-daily.md"
    filepath = out / filename
    filepath.write_text(content, encoding="utf-8")
    logger.info(f"日报已写入：{filepath}")
    return str(filepath)


def _save_to_db(db_path: str, date_str: str, file_path: str, summary: str):
    """记录日报元数据到 DB（列名与 init.py 保持一致：md_file_path）"""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            INSERT OR REPLACE INTO daily_reports (report_date, md_file_path, summary, generated_at)
            VALUES (?, ?, ?, ?)
        """, (date_str, file_path, summary[:500], datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"日报元数据写入 DB 失败：{e}")


# ────────────────────────────────────────────
# 5. 主入口
# ────────────────────────────────────────────

# 追问最长等待时间（用户没回答就超时强制生成）
INQUIRY_TIMEOUT_SEC = 10 * 60


def _get_pending_inquiry(db_path: str, date_str: str) -> Optional[Dict]:
    """查今天是否已有 inquiry 记录。返回 dict 或 None。"""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM daily_report_inquiries WHERE report_date = ?",
            (date_str,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"查询 inquiry 失败: {e}")
        return None


def _upsert_inquiry(db_path: str, date_str: str, gap: "InfoGap") -> None:
    """写入或覆盖今天的 pending inquiry（一天一条）。"""
    try:
        conn = sqlite3.connect(db_path)
        # 先删今天的（覆盖语义）
        conn.execute("DELETE FROM daily_report_inquiries WHERE report_date = ?", (date_str,))
        conn.execute(
            """INSERT INTO daily_report_inquiries
               (report_date, question, activity_id, time_range, app_summary, status, asked_at)
               VALUES (?, ?, ?, ?, ?, 'pending', datetime('now', 'localtime'))""",
            (date_str, gap.question, gap.activity_id, gap.time_range, gap.app_summary)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"写入 inquiry 失败: {e}")


def _mark_inquiry_timeout(db_path: str, date_str: str) -> None:
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE daily_report_inquiries SET status='timeout' WHERE report_date = ? AND status='pending'",
            (date_str,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"标记 inquiry timeout 失败: {e}")


def _push_inquiry_to_user(gap: "InfoGap", date_str: str) -> None:
    """通过 broadcaster 把追问推给前端（Live2D 弹窗 / 聊天框）。"""
    try:
        from bus.broadcaster import get_broadcaster
        get_broadcaster().broadcast_sync({
            "type": "navi:report_inquiry",
            "date": date_str,
            "question": gap.question,
            "time_range": gap.time_range,
            "app_summary": gap.app_summary,
            "duration_min": gap.duration_min,
            "timeout_sec": INQUIRY_TIMEOUT_SEC,
            "emotion": "curious",
        })
        logger.info(f"已推送追问到前端: {gap.question[:40]}")
    except Exception as e:
        logger.warning(f"推送追问失败: {e}")


def generate_daily_report(
    date_str: Optional[str] = None,
    skip_gap_check: bool = False,
    learning_summary: Optional[str] = None,
) -> Optional[str]:
    """
    生成指定日期的日报，写入 Obsidian 目录。

    Args:
        date_str: 'YYYY-MM-DD'，默认今天
        skip_gap_check: True 则跳过 gap 检测直接生成（超时 / 用户已回答时使用）
        learning_summary: 用户对今日学习内容的自述，注入到 prompt 增强日报质量

    Returns:
        日报文件路径，失败返回 None
        若进入追问等待状态（gap 已推送，等用户回答），返回 None 不生成
    """
    from config import get_config
    from llm_client import chat

    config = get_config()
    db_path    = str(Path(config.data_dir) / "app.db")
    date_str   = date_str or date.today().isoformat()
    output_dir = config.reports.get("output_dir", str(Path.home() / "obsidian"))

    logger.info(f"开始生成日报：{date_str} (skip_gap={skip_gap_check}, has_summary={bool(learning_summary)})")

    # Step 0: LLM 批量分类（把今天的 pending 活动先分类好再生成日报）
    try:
        from collector.llm_classifier import classify_pending_activities
        classified = classify_pending_activities(db_path, date_str)
        if classified > 0:
            logger.info(f"日报前 LLM 分类：{classified} 条活动已更新")
    except Exception as e:
        logger.warning(f"日报前 LLM 分类失败（不影响日报生成）: {e}")

    # Step 1: 活动记录
    activities      = _get_activities(db_path, date_str)
    activities_text = _activities_to_text(activities)
    logger.info(f"活动记录：{len(activities)} 条")

    # Step 1.5: 【P0-2】Gap 检测 → 追问 → 等待用户回答
    # 如果 skip_gap_check=False 且没有 learning_summary，就尝试发起追问
    if not skip_gap_check and not learning_summary:
        try:
            from report.gap_detector import detect_gap_via_llm
            gap = detect_gap_via_llm(activities, date_str)
            if gap is not None:
                # 入库 + 推送 + 启动超时定时器，本次先返回 None 不生成
                _upsert_inquiry(db_path, date_str, gap)
                _push_inquiry_to_user(gap, date_str)

                def _timeout_handler():
                    pending = _get_pending_inquiry(db_path, date_str)
                    if pending and pending.get("status") == "pending":
                        logger.info(f"inquiry 超时（{INQUIRY_TIMEOUT_SEC}s 无回答），强制生成日报")
                        _mark_inquiry_timeout(db_path, date_str)
                        try:
                            generate_daily_report(date_str=date_str, skip_gap_check=True)
                        except Exception as e:
                            logger.error(f"超时后强制生成日报失败: {e}")

                t = threading.Timer(INQUIRY_TIMEOUT_SEC, _timeout_handler)
                t.daemon = True
                t.start()
                logger.info(f"日报暂停，等待用户回答（{INQUIRY_TIMEOUT_SEC // 60} 分钟超时）")
                return None
        except Exception as e:
            logger.warning(f"gap 检测异常，跳过追问继续生成: {e}")

    # Step 2: 截图时间线（并行 Vision，生成时触发）
    screenshot_timeline = _get_screenshot_timeline(
        db_path, config.screenshot_dir, date_str,
        top_n=TOP_N_SCREENSHOTS, batch_size=BATCH_SIZE
    )
    logger.info("截图时间线生成完成")

    # Step 3: 历史记忆检索
    memory_context = _get_memory_context(activities_text, date_str)
    logger.info("历史记忆检索完成")

    # Step 4: 生成日报（注入 SOUL.md 人格 + 学习自述）
    user_prompt = _build_prompt(
        activities_text, screenshot_timeline, memory_context, date_str,
        learning_summary=learning_summary,
    )
    system_prompt = _build_system_prompt()
    logger.info(f"Prompt 总长度：user={len(user_prompt)} sys={len(system_prompt)} 字符")

    try:
        report_content = chat(
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
            max_tokens=REPORT_MAX_TOKENS,
        )
    except Exception as e:
        logger.error(f"日报生成失败：{e}")
        return None

    # Step 5: 写入 Obsidian
    filepath = _write_to_obsidian(report_content, date_str, output_dir)

    # Step 6: 存 DB + EpisodicMemory
    _save_to_db(db_path, date_str, filepath, report_content[:500])
    try:
        from memory.episodic_memory import get_episodic_memory
        em = get_episodic_memory(config.memory_dir)
        em.add_report(report_content[:1000], date_str)
    except Exception as e:
        logger.warning(f"日报写入 EpisodicMemory 失败：{e}")

    logger.info(f"✅ 日报生成完成：{filepath}")

    # 广播事件到所有 WebSocket 客户端（Live2D 联动）
    try:
        from bus.broadcaster import get_broadcaster
        get_broadcaster().broadcast_sync({
            "type": "navi:report_done",
            "date": date_str,
            "filepath": filepath,
            "emotion": "happy",
            "message": f"今天的日报已经生成好啦！快来看看吧~",
            "summary": report_content[:200],
        })
    except Exception as be:
        logger.debug(f"日报广播失败: {be}")

    return filepath
