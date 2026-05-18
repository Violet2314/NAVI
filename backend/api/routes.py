"""
Navi Backend API 路由
提供给前端使用的接口：状态、活动、配置
"""
import sqlite3
import yaml
from datetime import date, datetime
from pathlib import Path
import logging
from typing import Any, Dict, List

logger = logging.getLogger("navi.api")

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import get_config, NAVI_HOME

router = APIRouter()

# 全局引用采集组件状态（由 main.py 注入）
_collector_stats: Dict[str, Any] = {}

# ProactiveEngine 引用（由 main.py 注入）
_proactive_engine_ref = None

def set_proactive_engine_ref(engine):
    global _proactive_engine_ref
    _proactive_engine_ref = engine


def set_collector_stats(stats: Dict[str, Any]):
    global _collector_stats
    _collector_stats = stats


# 采集组件真实引用（由 main.py 注入）
_window_capture_ref = None
_screenshot_capture_ref = None
_tracker_ref = None
_blacklist_monitor_ref = None
_camera_capture_ref = None
_emotion_trigger_ref = None

def set_capture_refs(wc, sc):
    global _window_capture_ref, _screenshot_capture_ref
    _window_capture_ref = wc
    _screenshot_capture_ref = sc

def set_tracker_ref(tracker):
    global _tracker_ref
    _tracker_ref = tracker

def set_blacklist_monitor_ref(monitor):
    global _blacklist_monitor_ref
    _blacklist_monitor_ref = monitor

def set_camera_refs(camera, emotion_trigger):
    global _camera_capture_ref, _emotion_trigger_ref
    _camera_capture_ref = camera
    _emotion_trigger_ref = emotion_trigger

# ---- GET /api/status ----
@router.get("/api/status")
def get_status():
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    today = date.today().isoformat()

    # 实时拉取采集组件状态（本次启动后的计数）
    wc_stats = _window_capture_ref.get_stats() if _window_capture_ref else {}
    sc_stats = _screenshot_capture_ref.get_stats() if _screenshot_capture_ref else {}

    # 今日统计（从 DB 读全天合计，包含历次启动的数据）
    today_stats = {"total_segments": 0, "work_min": 0, "entertainment_min": 0, "screenshot_count": 0}
    today_window_total  = 0   # 今日窗口采集总次数（DB）
    today_screenshot_total = 0  # 今日截图总次数（DB）
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("""
            SELECT
              COUNT(*) as total,
              COALESCE(SUM(CASE WHEN app_category IN ('learning','work') THEN duration_sec ELSE 0 END), 0) as work_sec,
              COALESCE(SUM(CASE WHEN app_category = 'entertainment' THEN duration_sec ELSE 0 END), 0) as ent_sec
            FROM window_activities
            WHERE date(started_at) = ?
              AND parent_id IS NULL
        """, (today,)).fetchone()
        sc = conn.execute("SELECT COUNT(*) FROM screenshots WHERE date(captured_at) = ?", (today,)).fetchone()
        wc = conn.execute("SELECT COUNT(*) FROM window_activities WHERE date(started_at) = ?", (today,)).fetchone()
        conn.close()
        if row:
            today_stats["total_segments"]    = row[0]
            today_stats["work_min"]          = round(row[1] / 60)
            today_stats["entertainment_min"] = round(row[2] / 60)
        if sc:
            today_stats["screenshot_count"]  = sc[0]
            today_screenshot_total           = sc[0]
        if wc:
            today_window_total               = wc[0]
    except Exception:
        pass

    # 把今日 DB 合计注入到 stats，前端显示"今日合计"而非"本次启动"
    wc_stats = dict(wc_stats)
    sc_stats = dict(sc_stats)
    wc_stats["today_total"] = today_window_total
    sc_stats["today_total"] = today_screenshot_total

    # 摄像头状态
    camera_stats = {}
    if _camera_capture_ref:
        try:
            camera_stats = _camera_capture_ref.get_stats()
        except Exception:
            pass

    return {
        "window_capture": wc_stats,
        "screenshot_capture": sc_stats,
        "today": today_stats,
        "camera": camera_stats,
    }


# ---- GET /api/activities/today ----
@router.get("/api/activities/today")
def get_today_activities():
    """
    返回今日活动（树形结构）：大段带 children 子活动列表。
    大段 = parent_id IS NULL，子活动 = parent_id 指向大段 ID。
    """
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    today = date.today().isoformat()
    result = []

    # 1. 当前正在进行的段（内存里，未写DB）— 排最前面
    in_memory_started_at = None
    if _tracker_ref:
        current = _tracker_ref.get_current_segment()
        if current:
            result.append(current)
            in_memory_started_at = current.get("started_at")

    # 2. 已完成的大段 + 快照记录（DB里的）
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # 查询大段（parent_id IS NULL）
        parent_rows = conn.execute("""
            SELECT id, process_name, window_title, started_at, ended_at,
                   duration_sec, app_category, sentiment, is_synced, parent_id
            FROM window_activities
            WHERE date(started_at) = ? AND parent_id IS NULL
            ORDER BY started_at DESC
            LIMIT 100
        """, (today,)).fetchall()

        # 查询今日所有子活动
        child_rows = conn.execute("""
            SELECT id, process_name, window_title, started_at, ended_at,
                   duration_sec, app_category, parent_id
            FROM window_activities
            WHERE date(started_at) = ? AND parent_id IS NOT NULL
            ORDER BY started_at ASC
        """, (today,)).fetchall()
        conn.close()

        # 按 parent_id 分组子活动
        children_map = {}
        for c in child_rows:
            cd = dict(c)
            pid = cd.pop("parent_id")
            children_map.setdefault(pid, []).append(cd)

        for r in parent_rows:
            d = dict(r)
            row_id = d.get("id")   # 保留 id 传给前端
            d.pop("parent_id", None)
            is_synced = d.pop("is_synced", 0)

            if is_synced == -1:
                # 快照记录：内存里已有相同段时跳过
                if in_memory_started_at and d.get("started_at") == in_memory_started_at:
                    continue
                d["is_current"] = False
            else:
                d["is_current"] = False

            # 挂载子活动
            kids = children_map.get(row_id, [])
            d["children"] = kids

            # 从子活动计算 category_breakdown（DB记录没有此字段，需动态计算）
            breakdown: dict = {}
            for kid in kids:
                cat = kid.get("app_category") or "other"
                breakdown[cat] = breakdown.get(cat, 0) + (kid.get("duration_sec") or 0)
            if breakdown:
                d["category_breakdown"] = breakdown
                # 用 breakdown 最大值覆盖父段分类（保证与实时数据一致）
                # 注意：other/pending 不参与主导分类竞争，避免大量未分类活动
                # 错误地把有效分类的大段标记为"其他"
                SKIP_DOMINANT = {"other", ""}
                meaningful = {k: v for k, v in breakdown.items() if k not in SKIP_DOMINANT}
                if meaningful:
                    d["app_category"] = max(meaningful, key=lambda k: meaningful.get(k, 0))
                else:
                    d["app_category"] = "other"
            else:
                d["category_breakdown"] = {d.get("app_category", "other"): d.get("duration_sec", 0)}

            result.append(d)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    return result


# ---- POST /api/report/generate ----
@router.post("/api/report/generate")
def trigger_report(date: str | None = None, skip_gap_check: bool = False):
    """
    同步触发日报生成。

    流程：
      1. 第一次调用 → 检测到 gap → 入库 pending → 推送追问 → 返回 status=waiting_answer
      2. 用户回答 → /api/report/learning_answer → 内部用 learning_summary 重调生成
      3. 用户没答 → 10 分钟后定时器超时 → skip_gap_check=True 强制生成
      4. 用户想跳过 → 前端传 skip_gap_check=True，立即生成

    重复调用：会用新检测覆盖旧 pending（gap_detector 内部 LLM 是非确定性的）。
    """
    from report.report_generator import generate_daily_report, _get_pending_inquiry
    import logging, sqlite3
    from datetime import date as _date

    target_date = date or _date.today().isoformat()
    logger = logging.getLogger("navi.api")
    logger.info(f"▶ 开始生成日报：{target_date} (skip_gap={skip_gap_check})")

    filepath = generate_daily_report(target_date, skip_gap_check=skip_gap_check)

    # 没拿到 filepath 不一定是失败 — 可能进入了追问等待
    if not filepath:
        db_path = str(Path(get_config().data_dir) / "app.db")
        pending = _get_pending_inquiry(db_path, target_date)
        if pending and pending.get("status") == "pending":
            logger.info(f"日报进入追问等待: {pending.get('question', '')[:40]}")
            return {
                "status": "waiting_answer",
                "date": target_date,
                "question": pending.get("question"),
                "time_range": pending.get("time_range"),
                "app_summary": pending.get("app_summary"),
                "timeout_sec": 600,
                "message": "Navi 想问你一个问题，回答后会生成日报",
            }
        return JSONResponse(status_code=500, content={
            "status": "error",
            "message": "日报生成失败，请查看后端控制台日志"
        })

    # 读取生成结果摘要（截图数量 + 输出路径）
    db_path = str(Path(get_config().data_dir) / "app.db")
    screenshot_count = 0
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT COUNT(*) FROM screenshots WHERE date(captured_at)=? AND sent_to_llm=1",
            (target_date,)
        ).fetchone()
        conn.close()
        screenshot_count = row[0] if row else 0
    except Exception:
        pass

    logger.info(f"✅ 日报生成完成 → {filepath}（Vision 处理截图 {screenshot_count} 张）")
    return {
        "status": "done",
        "date": target_date,
        "filepath": filepath,
        "screenshot_count": screenshot_count,
    }


# ---- POST /api/report/learning_answer ----
@router.post("/api/report/learning_answer")
def submit_learning_answer(payload: dict):
    """
    用户回答今日学习追问后调用。

    Body: { "date": "YYYY-MM-DD", "answer": "今天在学 ..." }

    动作：
      1. 把 answer 写入 daily_report_inquiries.status=answered
      2. 用 learning_summary=answer 调 generate_daily_report 跳过 gap 检测，立即生成
    """
    from report.report_generator import generate_daily_report
    import logging, sqlite3
    from datetime import date as _date

    logger = logging.getLogger("navi.api")
    target_date = (payload.get("date") or _date.today().isoformat()).strip()
    answer = (payload.get("answer") or "").strip()

    if not answer:
        return JSONResponse(status_code=400, content={
            "status": "error", "message": "answer 不能为空"
        })

    # 1. 写入 answer
    db_path = str(Path(get_config().data_dir) / "app.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """UPDATE daily_report_inquiries
               SET status='answered', answer=?, answered_at=datetime('now', 'localtime')
               WHERE report_date=? AND status='pending'""",
            (answer, target_date)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"保存 inquiry answer 失败: {e}")
        return JSONResponse(status_code=500, content={
            "status": "error", "message": f"保存回答失败: {e}"
        })

    logger.info(f"用户已回答今日追问: {answer[:50]}")

    # 把用户的回答 + 后续系统回执写进 __report_inquiry__ 会话，保证对话闭环。
    try:
        from api.chat_routes import save_message, ensure_session, REPORT_INQUIRY_SESSION_ID, REPORT_INQUIRY_SESSION_TITLE
        ensure_session(REPORT_INQUIRY_SESSION_ID, title=REPORT_INQUIRY_SESSION_TITLE)
        save_message(REPORT_INQUIRY_SESSION_ID, "user", answer)
    except Exception as e:
        logger.warning(f"写入 __report_inquiry__ 用户消息失败（不影响主流程）: {e}")

    # 2. 立即生成日报，把 answer 当作 learning_summary 注入
    filepath = generate_daily_report(
        target_date,
        skip_gap_check=True,
        learning_summary=answer,
    )

    if not filepath:
        # 生成失败也在会话里留个痕
        try:
            from api.chat_routes import save_message, REPORT_INQUIRY_SESSION_ID
            save_message(REPORT_INQUIRY_SESSION_ID, "assistant",
                         "呜呜，日报生成出了点问题，稍后我再试一次~")
        except Exception:
            pass
        return JSONResponse(status_code=500, content={
            "status": "error",
            "message": "日报生成失败，请查看后端控制台日志"
        })

    # 成功后往会话里补一条反馈，让用户看到闭环
    try:
        from api.chat_routes import save_message, REPORT_INQUIRY_SESSION_ID
        save_message(
            REPORT_INQUIRY_SESSION_ID, "assistant",
            f"收到啦~ 我把今天的日报写好了，已经放在 Obsidian 里了 📝\n{filepath}"
        )
    except Exception as e:
        logger.warning(f"写入 __report_inquiry__ 完成消息失败: {e}")

    return {
        "status": "done",
        "date": target_date,
        "filepath": filepath,
        "message": "已根据你的回答生成日报",
    }


# ---- GET /api/report/status ----
@router.get("/api/report/status")
def get_report_status(date: str | None = None):
    """
    查今天的日报状态。前端 Tab 切回来时用它恢复 UI 状态（SSOT）。

    返回：
      - status: "none"           — 没生成过，按钮可点
      - status: "waiting_answer" — 有 pending 追问，按钮变"去聊天回答"
      - status: "done"           — 已生成
    """
    from datetime import date as _date
    target_date = date or _date.today().isoformat()
    db_path = str(Path(get_config().data_dir) / "app.db")

    try:
        conn = sqlite3.connect(db_path)
        done_row = conn.execute(
            "SELECT file_path, generated_at FROM daily_reports WHERE report_date=?",
            (target_date,)
        ).fetchone()
        if done_row:
            conn.close()
            return {
                "status": "done",
                "date": target_date,
                "filepath": done_row[0],
                "generated_at": done_row[1],
            }

        inq_row = conn.execute(
            """SELECT question, time_range, app_summary, asked_at
               FROM daily_report_inquiries
               WHERE report_date=? AND status='pending'""",
            (target_date,)
        ).fetchone()
        conn.close()
        if inq_row:
            return {
                "status": "waiting_answer",
                "date": target_date,
                "question": inq_row[0],
                "time_range": inq_row[1],
                "app_summary": inq_row[2],
                "asked_at": inq_row[3],
            }

        return {"status": "none", "date": target_date}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---- GET /api/report/today ----
@router.get("/api/report/today")
def get_today_report():
    """读取今日日报内容"""
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    today = date.today().isoformat()
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT file_path, summary, generated_at FROM daily_reports WHERE report_date=?",
            (today,)
        ).fetchone()
        conn.close()
        if not row:
            return {"exists": False}
        file_path, summary, generated_at = row
        content = Path(file_path).read_text(encoding="utf-8") if Path(file_path).exists() else summary
        return {"exists": True, "content": content, "generated_at": generated_at}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---- GET /api/debug/report ----
@router.get("/api/debug/report")
def debug_report():
    """诊断日报生成链路，快速定位 0 截图的原因"""
    from config import get_config
    from pathlib import Path
    import sqlite3, os
    from datetime import date as _date

    config      = get_config()
    db_path     = str(Path(config.data_dir) / "app.db")
    today       = _date.today().isoformat()
    screenshot_dir = config.screenshot_dir
    report_output  = config.reports.get("output_dir", "未配置")

    result = {
        "date": today,
        "screenshot_dir": screenshot_dir,
        "screenshot_dir_exists": os.path.isdir(screenshot_dir),
        "report_output_dir": report_output,
        "db_path": db_path,
        "db_exists": os.path.isfile(db_path),
    }

    try:
        conn = sqlite3.connect(db_path)

        # 1. 今日截图总数
        total = conn.execute(
            "SELECT COUNT(*) FROM screenshots WHERE date(captured_at)=?", (today,)
        ).fetchone()[0]
        result["screenshots_in_db_today"] = total

        # 2. 文件实际存在数量（抽查前20条）
        rows = conn.execute(
            "SELECT file_path FROM screenshots WHERE date(captured_at)=? LIMIT 20", (today,)
        ).fetchall()
        exist_count  = sum(1 for r in rows if os.path.isfile(r[0]))
        result["sample_file_exist"] = f"{exist_count}/{len(rows)} 文件实际存在"
        result["sample_paths"] = [r[0] for r in rows[:3]]  # 前3条路径示例

        # 3. sent_to_llm 数量
        sent = conn.execute(
            "SELECT COUNT(*) FROM screenshots WHERE date(captured_at)=? AND sent_to_llm=1", (today,)
        ).fetchone()[0]
        result["sent_to_llm_today"] = sent

        # 4. llm_description 非空数量
        described = conn.execute(
            "SELECT COUNT(*) FROM screenshots WHERE date(captured_at)=? AND llm_description IS NOT NULL", (today,)
        ).fetchone()[0]
        result["llm_described_today"] = described

        # 5. 尝试用 select_representative 拿结果
        from utils.screenshot_selector import select_representative
        reps = select_representative(today, screenshot_dir, db_path, top_n=5)
        result["select_representative_count"] = len(reps)
        if reps:
            result["select_representative_sample"] = [
                {"path": r["path"], "exists": os.path.isfile(r["path"]), "monitor": r.get("monitor")}
                for r in reps[:3]
            ]

        # 6. compress_for_llm 测试（取第一张）
        if reps:
            from utils.image_compress import compress_for_llm
            test_b64 = compress_for_llm(reps[0]["path"])
            result["compress_test"] = "✅ 成功" if test_b64 else "❌ 失败（compress_for_llm 返回 None）"
            result["compress_b64_len"] = len(test_b64) if test_b64 else 0

        conn.close()
    except Exception as e:
        result["error"] = str(e)

    return result


# ---- GET /api/config ----
@router.get("/api/config")
def get_config_api():
    config_path = NAVI_HOME / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---- POST /api/config ----
@router.post("/api/config")
def save_config_api(body: Dict[str, Any]):
    config_path = NAVI_HOME / "config.yaml"
    NAVI_HOME.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(body, f, allow_unicode=True, default_flow_style=False)
    # 清除 lru_cache，下次 get_config() 会重新读取
    get_config.cache_clear()

    # 热重载：把 collector 配置即时推给采集器，无需重启
    reloaded = []
    if _tracker_ref is not None and "collector" in body:
        try:
            _tracker_ref.update_config(body["collector"])
            reloaded.append("tracker")
        except Exception as e:
            pass  # 热重载失败不影响保存
    if _window_capture_ref is not None and "collector" in body:
        interval = body["collector"].get("window_interval_sec")
        if interval is not None:
            try:
                _window_capture_ref.set_interval(float(interval))
                reloaded.append("window_capture")
            except Exception:
                pass
    if _screenshot_capture_ref is not None and "collector" in body:
        interval = body["collector"].get("screenshot_interval_sec")
        if interval is not None:
            try:
                _screenshot_capture_ref.set_interval(float(interval))
                reloaded.append("screenshot_capture")
            except Exception:
                pass

    return {"ok": True, "hot_reloaded": reloaded}


# ============================================================
# Day 4: 黑名单 & 学习模式 APIs
# ============================================================

# ---- GET /api/processes ----
@router.get("/api/processes")
def get_running_processes():
    """返回当前所有运行中的进程名（去重、排序），供前端黑名单选择器使用。"""
    import psutil
    procs = set()
    for p in psutil.process_iter(["name"]):
        try:
            name = p.info["name"]
            if name:
                procs.add(name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"processes": sorted(procs, key=lambda x: x.lower())}


# ---- GET /api/study/status ----
@router.get("/api/study/status")
def get_study_status():
    """获取学习模式状态 + 黑名单列表"""
    if _blacklist_monitor_ref:
        return _blacklist_monitor_ref.get_status()
    return {"study_mode": False, "blacklist": [], "recent_kills": []}


# ---- POST /api/study/toggle ----
@router.post("/api/study/toggle")
def toggle_study_mode(body: Dict[str, Any]):
    """开启/关闭学习模式。body: { "enabled": true/false }"""
    enabled = bool(body.get("enabled", False))
    if _blacklist_monitor_ref:
        _blacklist_monitor_ref.set_study_mode(enabled)
    return {"ok": True, "study_mode": enabled}


# ---- GET /api/blacklist ----
@router.get("/api/blacklist")
def get_blacklist():
    """获取完整黑名单（含分类、情感等字段）"""
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, process_name, category, sentiment, is_blacklist FROM app_rules ORDER BY process_name"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---- POST /api/blacklist ----
@router.post("/api/blacklist")
def add_to_blacklist(body: Dict[str, Any]):
    """
    添加进程到黑名单。
    body: { "process_name": "bilibili.exe", "category": "entertainment" }
    """
    process_name = str(body.get("process_name", "")).strip()
    if not process_name:
        return JSONResponse(status_code=400, content={"error": "process_name 不能为空"})

    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    try:
        conn = sqlite3.connect(db_path)
        # UPSERT：已存在则更新 is_blacklist=1
        conn.execute("""
            INSERT INTO app_rules (process_name, category, sentiment, is_blacklist)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(process_name) DO UPDATE SET is_blacklist=1, category=excluded.category
        """, (
            process_name.lower(),
            body.get("category", "entertainment"),
            body.get("sentiment", "negative"),
        ))
        conn.commit()
        conn.close()
        # 热重载黑名单
        if _blacklist_monitor_ref:
            _blacklist_monitor_ref.reload_blacklist()
        return {"ok": True, "process_name": process_name}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---- DELETE /api/blacklist/{process_name} ----
@router.delete("/api/blacklist/{process_name}")
def remove_from_blacklist(process_name: str):
    """将进程从黑名单移除（is_blacklist=0，保留规则记录）"""
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE app_rules SET is_blacklist=0 WHERE process_name=?",
            (process_name.lower(),)
        )
        conn.commit()
        conn.close()
        if _blacklist_monitor_ref:
            _blacklist_monitor_ref.reload_blacklist()
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ============================================================
# 应用规则管理 APIs（自定义分类规则）
# ============================================================

@router.get("/api/app-rules")
def get_app_rules():
    """获取所有应用规则（含内置规则 + LLM缓存 + 用户自定义）"""
    from collector.activity_tracker import DEFAULT_CATEGORY
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")

    # 从 DB 拿已有规则
    db_rules: dict = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT process_name, category, is_user_defined, auto_tagged_at FROM app_rules ORDER BY process_name"
        ).fetchall()
        conn.close()
        for r in rows:
            db_rules[r["process_name"].lower()] = dict(r)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    # 合并内置规则（作为「默认」显示，但标注来源）
    result = []
    all_procs = set(db_rules.keys()) | set(DEFAULT_CATEGORY.keys())
    for proc in sorted(all_procs):
        db_r = db_rules.get(proc)
        builtin_cat = DEFAULT_CATEGORY.get(proc)
        if db_r:
            result.append({
                "process_name": proc,
                "category": db_r["category"],
                "is_user_defined": bool(db_r["is_user_defined"]),
                "source": "user" if db_r["is_user_defined"] else "llm",
                "builtin_category": builtin_cat,  # 内置规则（供前端对比显示）
                "auto_tagged_at": db_r.get("auto_tagged_at"),
            })
        else:
            result.append({
                "process_name": proc,
                "category": builtin_cat,
                "is_user_defined": False,
                "source": "builtin",
                "builtin_category": builtin_cat,
                "auto_tagged_at": None,
            })
    return result


@router.post("/api/app-rules")
def upsert_app_rule(body: Dict[str, Any]):
    """
    新增或更新一条用户自定义应用规则（is_user_defined=1）。
    body: { "process_name": "code.exe", "category": "work" }
    """
    process_name = str(body.get("process_name", "")).strip().lower()
    category = str(body.get("category", "")).strip().lower()
    if not process_name:
        return JSONResponse(status_code=400, content={"error": "process_name 不能为空"})
    valid = {"work", "learning", "entertainment", "communication", "utility", "other"}
    if category not in valid:
        return JSONResponse(status_code=400, content={"error": f"category 必须是 {valid} 之一"})

    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            INSERT INTO app_rules (process_name, category, is_user_defined, auto_tagged_at)
            VALUES (?, ?, 1, datetime('now'))
            ON CONFLICT(process_name) DO UPDATE SET
                category = excluded.category,
                is_user_defined = 1,
                auto_tagged_at = excluded.auto_tagged_at
        """, (process_name, category))
        conn.commit()
        conn.close()
        # 热重载规则到 tracker（如果 tracker 已绑定）
        if _tracker_ref:
            from collector.daemon import load_app_rules
            _tracker_ref.update_rules(load_app_rules(db_path))
        return {"ok": True, "process_name": process_name, "category": category}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.patch("/api/activities/{activity_id}/category")
async def patch_activity_category(activity_id: int, request: Request):
    """
    手动修改单条 activity 记录的分类，同时写 app_rules（is_user_defined=1）。
    body: { "category": "work" }
    """
    body = await request.json()
    category = str(body.get("category", "")).strip().lower()
    valid = {"learning", "work", "entertainment", "utility", "other"}
    if category not in valid:
        return JSONResponse(status_code=400, content={"error": f"category 必须是 {valid} 之一"})

    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    try:
        conn = sqlite3.connect(db_path)
        # 1. 更新单条记录
        cur = conn.execute(
            "UPDATE window_activities SET app_category=?, classification_source='user' WHERE id=?",
            (category, activity_id)
        )
        if cur.rowcount == 0:
            conn.close()
            return JSONResponse(status_code=404, content={"error": "记录不存在"})
        # 2. 顺带把进程规则也写进去（is_user_defined=1），后续新记录也生效
        row = conn.execute(
            "SELECT process_name FROM window_activities WHERE id=?", (activity_id,)
        ).fetchone()
        if row:
            proc = row[0].lower()
            conn.execute("""
                INSERT INTO app_rules (process_name, category, is_user_defined, auto_tagged_at)
                VALUES (?, ?, 1, datetime('now'))
                ON CONFLICT(process_name) DO UPDATE SET
                    category=excluded.category,
                    is_user_defined=1,
                    auto_tagged_at=excluded.auto_tagged_at
            """, (proc, category))
        conn.commit()
        conn.close()
        # 3. 热更新 tracker（如果 daemon 在跑）
        try:
            from collector.daemon import load_app_rules
            if _tracker_ref:
                _tracker_ref.update_rules(load_app_rules(db_path))
        except Exception:
            pass
        return {"ok": True, "id": activity_id, "category": category}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.delete("/api/app-rules/{process_name}")
def delete_app_rule(process_name: str):
    """删除用户自定义规则（仅删 is_user_defined=1 的，LLM缓存和内置不允许删）"""
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "DELETE FROM app_rules WHERE process_name=? AND is_user_defined=1",
            (process_name.lower(),)
        )
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            return JSONResponse(status_code=404, content={"error": "规则不存在或为系统规则，不可删除"})
        if _tracker_ref:
            from collector.daemon import load_app_rules
            _tracker_ref.update_rules(load_app_rules(db_path))
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ============================================================
# LLM 配置 APIs（多厂商支持）
# ============================================================

# 支持的厂商列表
LLM_PROVIDERS = [
    {"id": "doubao",      "name": "豆包 (火山引擎)",  "base_url": "https://ark.cn-beijing.volces.com/api/v3", "vision": True},
    {"id": "deepseek",    "name": "DeepSeek",          "base_url": "https://api.deepseek.com",               "vision": False},
    {"id": "openai",      "name": "OpenAI",             "base_url": "https://api.openai.com/v1",              "vision": True},
    {"id": "anthropic",   "name": "Anthropic (Claude)", "base_url": "https://api.anthropic.com",              "vision": True},
    {"id": "gemini",      "name": "Google Gemini",      "base_url": "https://generativelanguage.googleapis.com/v1beta", "vision": True},
    {"id": "minimax",     "name": "MiniMax（国内 sk-api-xxx）", "base_url": "https://api.minimax.chat/v1",  "vision": True},
    {"id": "minimax_int", "name": "MiniMax（国际 eyJ...）",     "base_url": "https://api.minimax.io/v1",    "vision": True},
    {"id": "moonshot",    "name": "Moonshot (Kimi)",    "base_url": "https://api.moonshot.cn/v1",             "vision": False},
    {"id": "qwen",        "name": "通义千问 (DashScope)", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "vision": True},
    {"id": "siliconflow", "name": "硅基流动",           "base_url": "https://api.siliconflow.cn/v1",          "vision": True},
    {"id": "openrouter",  "name": "OpenRouter (聚合)",  "base_url": "https://openrouter.ai/api/v1",           "vision": True},
    {"id": "custom",      "name": "自定义 OpenAI 兼容",  "base_url": "",                                       "vision": True},
]


@router.get("/api/llm/providers")
def get_llm_providers():
    """返回支持的 LLM 厂商列表"""
    return LLM_PROVIDERS


@router.get("/api/llm/models")
def get_llm_models(provider: str, api_key: str = "", base_url: str = ""):
    """
    拉取指定厂商的可用模型列表。
    api_key / base_url 从前端传入（来自当前已填写的配置）。
    失败时返回厂商预设的常用模型列表。
    """
    # 预设常用模型（2025年最新，当 API 拉取失败时兜底）
    PRESET: Dict[str, List[str]] = {
        "doubao": [
            # Vision 模型
            "doubao-vision-pro-32k", "doubao-vision-lite-32k",
            "doubao-vision-pro-4k", "doubao-vision-lite-4k",
            # Chat 模型
            "doubao-pro-256k", "doubao-pro-128k", "doubao-pro-32k", "doubao-pro-4k",
            "doubao-lite-256k", "doubao-lite-128k", "doubao-lite-32k", "doubao-lite-4k",
        ],
        "deepseek": [
            "deepseek-chat",       # DeepSeek-V3，主力对话
            "deepseek-reasoner",   # DeepSeek-R1，推理
        ],
        "openai": [
            "gpt-4o", "gpt-4o-mini",
            "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            "o3-mini", "o4-mini",
        ],
        "anthropic": [
            "claude-opus-4-5", "claude-sonnet-4-5",
            "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ],
        "gemini": [
            "gemini-2.5-pro-preview-03-25",
            "gemini-2.0-flash", "gemini-2.0-flash-lite",
            "gemini-1.5-pro", "gemini-1.5-flash",
        ],
        "minimax": [
            # MiniMax 不提供 /models 接口，使用官方文档预设列表
            "MiniMax-VL-01",          # Vision + Chat
            "MiniMax-M2.5",           # Chat 旗舰
            "MiniMax-M2.5-Flash",     # Chat 快速
            "MiniMax-Text-01",        # 长文本
            "embo-01",                # Embedding
        ],
        "minimax_int": [
            "MiniMax-VL-01",
            "MiniMax-M2.5", "MiniMax-M2.5-Flash",
            "MiniMax-Text-01",
            "embo-01",
        ],
        "moonshot": [
            "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k",
            "moonshot-v1-128k-vision-preview",
        ],
        "qwen": [
            # Vision
            "qwen-vl-max", "qwen-vl-max-latest", "qwen-vl-plus",
            # Chat
            "qwen-max", "qwen-max-latest",
            "qwen-plus", "qwen-plus-latest",
            "qwen-turbo", "qwen-turbo-latest",
            "qwen-long",
        ],
        "siliconflow": [
            "Qwen/Qwen2.5-VL-72B-Instruct", "Qwen/Qwen2.5-VL-7B-Instruct",
            "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1",
            "Pro/deepseek-ai/DeepSeek-R1", "Pro/deepseek-ai/DeepSeek-V3",
            "meta-llama/Llama-3.3-70B-Instruct",
        ],
        "openrouter": [
            "google/gemini-2.0-flash-001",
            "anthropic/claude-3.5-sonnet",
            "deepseek/deepseek-chat-v3-0324:free",
            "deepseek/deepseek-r1:free",
            "openai/gpt-4o",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
        "custom": [],
    }

    preset = PRESET.get(provider, [])

    # MiniMax 不支持 OpenAI 标准 /models 接口，直接返回预设
    if provider == "minimax":
        return {"models": preset, "source": "preset"}

    # 其余厂商尝试动态拉取（走 OpenAI /models 接口）
    if api_key and base_url:
        try:
            from openai import OpenAI
            # api_key 安全清洗：去掉换行、空格、注释
            clean_key = api_key.strip().split()[0]
            client = OpenAI(api_key=clean_key, base_url=base_url, timeout=8)
            models_resp = client.models.list()
            ids = sorted([m.id for m in models_resp.data])
            if ids:
                return {"models": ids, "source": "api"}
        except Exception as e:
            logger.debug(f"[models] 动态拉取失败({provider}): {e}")

    return {"models": preset, "source": "preset"}


@router.get("/api/llm/config")
def get_llm_config():
    """获取当前 LLM 配置（不返回完整 API Key，只返回前4位用于显示）"""
    config_path = NAVI_HOME / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    llm = raw.get("llm", {})
    # 直接返回明文，前端 input type=password 不会显示
    return {
        "vision_provider":  llm.get("vision_provider", ""),
        "vision_model":     llm.get("vision_model", ""),
        "vision_api_key":   llm.get("vision_api_key", ""),
        "vision_base_url":  llm.get("vision_base_url", ""),
        "text_provider":    llm.get("text_provider", ""),
        "text_model":       llm.get("text_model", ""),
        "text_api_key":     llm.get("text_api_key", ""),
        "text_base_url":    llm.get("text_base_url", ""),
        "emb_provider":     llm.get("emb_provider", ""),
        "emb_model":        llm.get("emb_model", ""),
        "emb_api_key":      llm.get("emb_api_key", ""),
        "emb_base_url":     llm.get("emb_base_url", ""),
    }


@router.get("/api/llm/active")
def get_llm_active():
    """
    返回当前 llm_client 实际生效的配置（Key 脱敏）。
    用于前端「查看实际生效配置」按钮，方便排查 UI 配置是否真的生效。
    """
    from llm_client import _load_llm_cfg
    cfg = _load_llm_cfg()
    return {
        "vision": {
            "provider":  cfg.get("vision_provider", "（未设置）"),
            "model":     cfg.get("vision_model", ""),
            "api_key":   cfg.get("vision_api_key", "（未配置）"),
            "base_url":  cfg.get("vision_base_url", ""),
        },
        "text": {
            "provider":  cfg.get("text_provider", "（未设置）"),
            "model":     cfg.get("text_model", ""),
            "api_key":   cfg.get("text_api_key", "（未配置）"),
            "base_url":  cfg.get("text_base_url", ""),
        },
        "emb": {
            "provider":  cfg.get("emb_provider", "（未设置）"),
            "model":     cfg.get("emb_model", ""),
            "api_key":   cfg.get("emb_api_key", "（未配置）"),
            "base_url":  cfg.get("emb_base_url", ""),
        },
        "config_source": str(Path.home() / ".navi" / "config.yaml"),
    }


# ============================================================
# Camera API
# ============================================================

@router.get("/api/camera/status")
def get_camera_status():
    """返回摄像头状态 + 当前情绪"""
    if not _camera_capture_ref:
        return {"enabled": False, "running": False}
    stats = _camera_capture_ref.get_stats()
    emotion_summary = {}
    if _emotion_trigger_ref:
        try:
            emotion_summary = _emotion_trigger_ref.get_recent_summary()
        except Exception:
            pass
    return {
        "enabled": True,
        "running": _camera_capture_ref._running if hasattr(_camera_capture_ref, '_running') else False,
        "user_present": stats.get("user_present", False),
        "current_emotion": stats.get("current_emotion"),
        "current_confidence": stats.get("emotion_confidence", 0),
        "stats": stats,
        "emotion_summary": emotion_summary,
    }


@router.post("/api/camera/toggle")
def toggle_camera(body: Dict[str, Any]):
    """开启/关闭摄像头（支持首次从零创建实例）"""
    global _camera_capture_ref, _emotion_trigger_ref
    enabled = bool(body.get("enabled", False))

    try:
        if enabled:
            # 如果实例已存在且在运行，直接返回
            if _camera_capture_ref and getattr(_camera_capture_ref, '_running', False):
                return {"ok": True, "enabled": True, "msg": "already running"}

            # 首次启动：创建实例
            if not _camera_capture_ref:
                from collector.camera_capture import CameraCapture
                from collector.emotion_trigger import EmotionTrigger
                config = get_config()
                db_path = str(Path(config.data_dir) / "app.db")
                model_dir = str(Path(config.data_dir) / "models")
                Path(model_dir).mkdir(parents=True, exist_ok=True)

                _camera_capture_ref = CameraCapture(
                    capture_interval=float(config.collector.get("camera_interval_sec", 5)),
                    emotion_every_n=int(config.collector.get("emotion_every_n", 6)),
                    camera_index=int(config.collector.get("camera_index", 0)),
                    model_dir=model_dir,
                    confidence_threshold=float(config.collector.get("camera_confidence_threshold", 0.7)),
                )
                _emotion_trigger_ref = EmotionTrigger(db_path=db_path)

                # 接线：摄像头回调 → tracker + emotion_trigger
                if _tracker_ref:
                    _local_tracker = _tracker_ref
                    def on_camera_captured(results):
                        for r in results:
                            _local_tracker.on_presence_update(r.get("user_present", False))
                            if r.get("emotion"):
                                _local_tracker.on_emotion_update(r["emotion"])
                                if _emotion_trigger_ref:
                                    _emotion_trigger_ref.on_emotion(r)
                                    _emotion_trigger_ref.save_record(r)
                    _camera_capture_ref.set_callback(on_camera_captured)

            ok = _camera_capture_ref.start()
            if not ok:
                return JSONResponse(status_code=500, content={
                    "error": "摄像头启动失败，请检查摄像头连接和模型文件是否存在"
                })
            return {"ok": True, "enabled": True}
        else:
            # 关闭
            if _camera_capture_ref:
                _camera_capture_ref.stop()
            return {"ok": True, "enabled": False}
    except ImportError as e:
        return JSONResponse(status_code=500, content={
            "error": f"依赖未安装: {e}。请运行: uv add opencv-python-headless onnxruntime"
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/camera/emotions")
def get_recent_emotions():
    """返回最近的情绪记录"""
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, detected_at, user_present, emotion, confidence, face_count
            FROM emotion_records
            ORDER BY detected_at DESC
            LIMIT 100
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/api/llm/config")
def save_llm_config(body: Dict[str, Any]):
    """
    保存 LLM 配置。
    如果 api_key 以 '****' 结尾（脱敏值），则保留原值不覆盖。
    """
    config_path = NAVI_HOME / "config.yaml"
    NAVI_HOME.mkdir(parents=True, exist_ok=True)

    # 读取现有配置
    raw: Dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    existing_llm = raw.get("llm", {})

    # 直接保存明文，config.yaml 是本地文件无需脱敏
    # 如果前端传来的值为空，保留原有值（防止误清空）
    def keep_or_update(new_val: Any, old_key: str) -> str:
        if new_val is None or new_val == "":
            return existing_llm.get(old_key, "")
        return new_val

    raw["llm"] = {
        **existing_llm,
        "vision_provider": keep_or_update(body.get("vision_provider"), "vision_provider"),
        "vision_model":    keep_or_update(body.get("vision_model"),    "vision_model"),
        "vision_api_key":  keep_or_update(body.get("vision_api_key"),  "vision_api_key"),
        "vision_base_url": keep_or_update(body.get("vision_base_url"), "vision_base_url"),
        "text_provider":   keep_or_update(body.get("text_provider"),   "text_provider"),
        "text_model":      keep_or_update(body.get("text_model"),      "text_model"),
        "text_api_key":    keep_or_update(body.get("text_api_key"),    "text_api_key"),
        "text_base_url":   keep_or_update(body.get("text_base_url"),   "text_base_url"),
        "emb_provider":    keep_or_update(body.get("emb_provider"),    "emb_provider"),
        "emb_model":       keep_or_update(body.get("emb_model"),       "emb_model"),
        "emb_api_key":     keep_or_update(body.get("emb_api_key"),     "emb_api_key"),
        "emb_base_url":    keep_or_update(body.get("emb_base_url"),    "emb_base_url"),
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
    get_config.cache_clear()
    return {"ok": True}


# ============================================================
# 主动对话引擎 APIs
# ============================================================

@router.get("/api/proactive/config")
def get_proactive_config():
    """获取主动对话引擎当前参数"""
    from proactive import constants as C

    return {
        # 引擎运行参数
        "patrol_interval_sec": C.PATROL_INTERVAL_SEC,
        # 触发阈值 & 紧急度
        "long_work_threshold_min": C.LONG_WORK_THRESHOLD_MIN,
        "slacking_threshold_min": C.SLACKING_THRESHOLD_MIN,
        "idle_threshold_min": C.IDLE_THRESHOLD_MIN,
        "periodic_chat_threshold_hours": C.PERIODIC_CHAT_THRESHOLD_HOURS,
        # 门控参数
        "gate_min_interval_min": C.GATE_MIN_INTERVAL_MIN,
        "gate_max_daily_proactive": C.GATE_MAX_DAILY_PROACTIVE,
        "gate_quiet_hours_start": C.GATE_QUIET_HOURS_START,
        "gate_quiet_hours_end": C.GATE_QUIET_HOURS_END,
        "gate_base_threshold": C.GATE_BASE_THRESHOLD,
        # 经验学习
        "feedback_ignore_timeout_sec": C.FEEDBACK_IGNORE_TIMEOUT_SEC,
        "silent_mode_default_hours": C.SILENT_MODE_DEFAULT_HOURS,
    }


@router.post("/api/proactive/config")
def save_proactive_config(body: Dict[str, Any]):
    """
    修改主动对话引擎参数（运行时即时生效）。
    只需传入要修改的字段，未传的保持不变。
    """
    from proactive import constants as C

    updated = []

    FIELDS = {
        "patrol_interval_sec":           ("PATROL_INTERVAL_SEC", int),
        "long_work_threshold_min":       ("LONG_WORK_THRESHOLD_MIN", int),
        "slacking_threshold_min":        ("SLACKING_THRESHOLD_MIN", int),
        "idle_threshold_min":            ("IDLE_THRESHOLD_MIN", int),
        "periodic_chat_threshold_hours": ("PERIODIC_CHAT_THRESHOLD_HOURS", int),
        "gate_min_interval_min":         ("GATE_MIN_INTERVAL_MIN", int),
        "gate_max_daily_proactive":      ("GATE_MAX_DAILY_PROACTIVE", int),
        "gate_quiet_hours_start":        ("GATE_QUIET_HOURS_START", int),
        "gate_quiet_hours_end":          ("GATE_QUIET_HOURS_END", int),
        "gate_base_threshold":           ("GATE_BASE_THRESHOLD", float),
        "feedback_ignore_timeout_sec":   ("FEEDBACK_IGNORE_TIMEOUT_SEC", int),
        "silent_mode_default_hours":     ("SILENT_MODE_DEFAULT_HOURS", float),
    }

    for key, (const_name, cast) in FIELDS.items():
        if key in body:
            val = cast(body[key])
            setattr(C, const_name, val)
            updated.append(key)

    # 同步更新 GateKeeper 的运行时实例参数
    if _proactive_engine_ref:
        gate = _proactive_engine_ref.gate
        if "gate_min_interval_min" in body:
            gate.min_interval_minutes = int(body["gate_min_interval_min"])
        if "gate_max_daily_proactive" in body:
            gate.max_daily_proactive = int(body["gate_max_daily_proactive"])
        if "gate_quiet_hours_start" in body or "gate_quiet_hours_end" in body:
            gate.quiet_hours = (C.GATE_QUIET_HOURS_START, C.GATE_QUIET_HOURS_END)
        if "gate_base_threshold" in body:
            gate.base_threshold = float(body["gate_base_threshold"])
        if "patrol_interval_sec" in body:
            _proactive_engine_ref.patrol_interval = int(body["patrol_interval_sec"])

    # 持久化到 config.yaml
    config_path = NAVI_HOME / "config.yaml"
    raw = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    raw["proactive"] = {k: body[k] for k in body if k in FIELDS}
    # 合并已有的 proactive 配置
    existing_p = raw.get("proactive", {})
    for k in body:
        if k in FIELDS:
            existing_p[k] = FIELDS[k][1](body[k])
    raw["proactive"] = existing_p

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
    get_config.cache_clear()

    return {"ok": True, "updated": updated}


@router.get("/api/proactive/status")
def get_proactive_status():
    """获取主动对话引擎运行状态（用于调试）"""
    if not _proactive_engine_ref:
        return {"running": False, "error": "ProactiveEngine 未初始化"}
    return _proactive_engine_ref.get_stats()


# ============================================================
# Skill 管理 APIs
# ============================================================

@router.get("/api/skills")
def list_skills():
    """列出所有 Skills（builtin + workspace）"""
    from agent.skills import SkillsLoader, BUILTIN_SKILLS_DIR
    from config import get_workspace_path
    from config.loader import load_config, get_config_path
    nb_cfg = load_config(get_config_path())
    workspace = get_workspace_path(nb_cfg.agents.defaults.workspace)
    loader = SkillsLoader(workspace, BUILTIN_SKILLS_DIR)
    all_skills = loader.list_skills(filter_unavailable=False)
    result = []
    for s in all_skills:
        meta = loader.get_skill_metadata(s["name"]) or {}
        result.append({
            "name": s["name"],
            "description": meta.get("description", ""),
            "source": s["source"],
            "path": s["path"],
        })
    return result


@router.delete("/api/skills/{skill_name}")
def delete_skill(skill_name: str):
    """删除 workspace 中的 Skill"""
    import shutil
    from config import get_workspace_path
    from config.loader import load_config, get_config_path
    nb_cfg = load_config(get_config_path())
    workspace = get_workspace_path(nb_cfg.agents.defaults.workspace)
    skill_dir = workspace / "skills" / skill_name
    if not skill_dir.exists():
        return JSONResponse(status_code=404, content={"error": f"Skill '{skill_name}' 不存在或为内置 Skill，无法删除"})
    shutil.rmtree(skill_dir)
    return {"ok": True}


# ============================================================
# MCP 服务器配置 APIs
# ============================================================

def _get_nb_config_path() -> Path:
    """获取 navi 配置文件路径（~/.navi/config.json 或由 set_config_path 指定）"""
    from config.loader import get_config_path
    return get_config_path()


def _load_nb_raw() -> dict:
    """读取 navi 原始配置 JSON"""
    import json
    p = _get_nb_config_path()
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_nb_raw(raw: dict) -> None:
    """写回 navi 配置 JSON"""
    import json
    p = _get_nb_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)


@router.get("/api/mcp/servers")
def get_mcp_servers():
    """获取当前配置的 MCP 服务器列表"""
    raw = _load_nb_raw()
    servers: dict = raw.get("tools", {}).get("mcpServers", {})
    result = []
    for name, cfg in servers.items():
        result.append({"name": name, **cfg})
    return result


@router.post("/api/mcp/servers")
def upsert_mcp_server(body: Dict[str, Any]):
    """新增或更新一个 MCP 服务器配置"""
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "name 不能为空"})

    raw = _load_nb_raw()
    raw.setdefault("tools", {}).setdefault("mcpServers", {})

    server_cfg: dict = {}
    if body.get("type"):
        server_cfg["type"] = body["type"]
    if body.get("command"):
        server_cfg["command"] = body["command"]
    if body.get("args"):
        server_cfg["args"] = body["args"] if isinstance(body["args"], list) else body["args"].split()
    if body.get("env"):
        server_cfg["env"] = body["env"]
    if body.get("url"):
        server_cfg["url"] = body["url"]
    if body.get("headers"):
        server_cfg["headers"] = body["headers"]
    server_cfg["toolTimeout"] = int(body.get("tool_timeout", 30))
    enabled = body.get("enabled_tools", ["*"])
    server_cfg["enabledTools"] = enabled if isinstance(enabled, list) else [enabled]

    raw["tools"]["mcpServers"][name] = server_cfg
    _save_nb_raw(raw)
    return {"ok": True, "name": name, "note": "MCP 服务器配置已保存，重启后端后生效"}


@router.delete("/api/mcp/servers/{server_name}")
def delete_mcp_server(server_name: str):
    """删除一个 MCP 服务器配置"""
    raw = _load_nb_raw()
    servers: dict = raw.get("tools", {}).get("mcpServers", {})
    if server_name not in servers:
        return JSONResponse(status_code=404, content={"error": f"服务器 '{server_name}' 不存在"})
    del servers[server_name]
    _save_nb_raw(raw)
    return {"ok": True}


@router.post("/api/proactive/test")
def test_proactive_trigger():
    """手动触发一次主动对话测试（调试用）"""
    if not _proactive_engine_ref:
        return JSONResponse(status_code=500, content={"error": "ProactiveEngine 未初始化"})

    from proactive.triggers import Trigger
    test_trigger = Trigger(
        type="periodic_chat",
        urgency=0.8,
        context={"reason": "手动测试触发", "current_time": datetime.now().isoformat()},
    )
    import asyncio
    loop = _proactive_engine_ref._loop
    if loop:
        asyncio.run_coroutine_threadsafe(
            _proactive_engine_ref._process_trigger(test_trigger), loop
        )
        return {"ok": True, "message": "测试触发已发送，请查看主动对话会话"}
    return JSONResponse(status_code=500, content={"error": "事件循环未就绪"})


# ---- GET /api/live2d/models ----
@router.get("/api/live2d/models")
def get_live2d_models():
    """
    自动扫描 frontend/public/live2d 目录，找到所有 .model3.json 文件，
    返回可用模型列表。增删模型文件夹后无需手动维护 models.json。
    """
    import os

    # 定位 frontend/public/live2d 目录
    backend_dir = Path(__file__).resolve().parent.parent  # backend/
    project_root = backend_dir.parent  # Navi/
    live2d_dir = project_root / "frontend" / "public" / "live2d"

    if not live2d_dir.is_dir():
        logger.warning(f"Live2D 目录不存在: {live2d_dir}")
        return []

    models = []
    # 遍历 live2d 下的一级子目录，每个子目录对应一个模型
    for entry in sorted(live2d_dir.iterdir()):
        if not entry.is_dir():
            continue
        # 在该目录及其子目录中查找 .model3.json
        model3_files = list(entry.rglob("*.model3.json"))
        if not model3_files:
            continue
        # 取第一个找到的 .model3.json
        model3_path = model3_files[0]
        rel = model3_path.relative_to(live2d_dir)
        src = f"/live2d/{rel.as_posix()}"
        model_name = entry.name
        model_id = model_name.lower().replace(" ", "-")
        models.append({
            "id": model_id,
            "name": model_name,
            "src": src,
        })

    return models
