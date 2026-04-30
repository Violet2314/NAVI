"""
InsightsEngine — 会话行为分析引擎（Phase 2）

基于 Hermes agent/insights.py 设计，适配 Navi 的 SQLite 数据结构。

功能：
  - 分析工具调用模式（最常用 / 最失败）
  - 统计会话活跃度 / 消息量
  - 识别用户行为规律（活跃时段、常用功能）
  - 生成自然语言洞察报告，注入 AI 系统提示

使用方式：
    engine = InsightsEngine(db_path)
    report = engine.generate(days=30)
    summary = engine.format_summary(report)     # 注入系统提示的简短版
    detail = engine.format_terminal(report)     # 完整报告（/insights 命令时用）
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class InsightsEngine:
    """
    分析 Navi 的会话历史和工具使用数据，生成行为洞察。

    直接查询 SQLite，不持有长期连接（每次 generate 时开关）。
    """

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 主入口 ────────────────────────────────────────────────────────────────

    def generate(self, days: int = 30) -> dict[str, Any]:
        """
        生成完整的行为洞察报告。

        Args:
            days: 回溯天数（默认 30 天）

        Returns:
            结构化报告字典
        """
        cutoff = time.time() - days * 86400

        with self._conn() as conn:
            sessions = self._get_sessions(conn, cutoff)
            tool_usage = self._get_tool_usage(conn, cutoff)
            activity = self._get_activity_patterns(conn, cutoff)
            facts_count = self._get_facts_count(conn)

        if not sessions:
            return {
                "days": days,
                "empty": True,
                "generated_at": time.time(),
                "overview": {},
                "tools": [],
                "activity": {},
                "facts_count": facts_count,
            }

        overview = self._compute_overview(sessions)
        tools = self._compute_tool_breakdown(tool_usage)
        hourly = self._compute_hourly_pattern(activity)

        return {
            "days": days,
            "empty": False,
            "generated_at": time.time(),
            "overview": overview,
            "tools": tools,
            "activity": {"hourly": hourly},
            "facts_count": facts_count,
        }

    # ── SQL 查询 ──────────────────────────────────────────────────────────────

    def _get_sessions(self, conn: sqlite3.Connection, cutoff: float) -> list[dict]:
        try:
            rows = conn.execute(
                """SELECT id, started_at, ended_at, message_count,
                          tool_call_count, model, source
                   FROM agent_sessions
                   WHERE started_at >= ?
                   ORDER BY started_at DESC""",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []  # 表不存在时（首次启动）安全降级

    def _get_tool_usage(self, conn: sqlite3.Connection, cutoff: float) -> list[dict]:
        try:
            rows = conn.execute(
                """SELECT tool_name,
                          COUNT(*) as total,
                          SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as failures,
                          AVG(duration_ms) as avg_ms
                   FROM tool_call_log
                   WHERE called_at >= ?
                   GROUP BY tool_name
                   ORDER BY total DESC""",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _get_activity_patterns(self, conn: sqlite3.Connection, cutoff: float) -> list[dict]:
        """按小时统计对话活跃度（基于 agent_sessions.started_at）。"""
        try:
            rows = conn.execute(
                """SELECT started_at FROM agent_sessions
                   WHERE started_at >= ?""",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _get_facts_count(self, conn: sqlite3.Connection) -> int:
        """获取当前 FactMemory 中的事实总数。"""
        try:
            row = conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    # ── 数据计算 ──────────────────────────────────────────────────────────────

    def _compute_overview(self, sessions: list[dict]) -> dict[str, Any]:
        total = len(sessions)
        total_msgs = sum(s.get("message_count") or 0 for s in sessions)
        total_tools = sum(s.get("tool_call_count") or 0 for s in sessions)

        durations = []
        for s in sessions:
            if s.get("started_at") and s.get("ended_at"):
                d = s["ended_at"] - s["started_at"]
                if 0 < d < 86400:
                    durations.append(d)

        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "total_sessions": total,
            "total_messages": total_msgs,
            "total_tool_calls": total_tools,
            "avg_session_duration_sec": round(avg_duration),
            "avg_messages_per_session": round(total_msgs / total, 1) if total else 0,
        }

    def _compute_tool_breakdown(self, tool_usage: list[dict]) -> list[dict[str, Any]]:
        result = []
        for row in tool_usage[:15]:  # 最多展示 15 个工具
            total = row.get("total") or 0
            failures = row.get("failures") or 0
            result.append({
                "name": row["tool_name"],
                "total": total,
                "failures": failures,
                "success_rate": round((total - failures) / total, 2) if total else 1.0,
                "avg_ms": round(row.get("avg_ms") or 0),
            })
        return result

    def _compute_hourly_pattern(self, activity: list[dict]) -> list[int]:
        """计算 0-23 小时的会话分布（活跃时段检测）。"""
        hourly = [0] * 24
        for row in activity:
            ts = row.get("started_at")
            if ts:
                try:
                    hour = datetime.fromtimestamp(ts).hour
                    hourly[hour] += 1
                except Exception:
                    pass
        return hourly

    # ── 格式化输出 ────────────────────────────────────────────────────────────

    def format_summary(self, report: dict[str, Any]) -> str:
        """
        生成注入系统提示的简短洞察（≤ 200 字符）。
        用于 on_turn_start 时附加到上下文，帮助 AI 了解用户习惯。
        """
        if report.get("empty"):
            return ""

        ov = report.get("overview", {})
        facts = report.get("facts_count", 0)
        days = report.get("days", 30)

        # 找最活跃时段
        hourly = report.get("activity", {}).get("hourly", [0] * 24)
        peak_hour = hourly.index(max(hourly)) if max(hourly) > 0 else -1

        # 最常用工具
        tools = report.get("tools", [])
        top_tool = tools[0]["name"] if tools else None

        parts = [f"[行为洞察·{days}天]"]
        parts.append(f"共 {ov.get('total_sessions', 0)} 次会话，{ov.get('total_messages', 0)} 条消息")
        if facts:
            parts.append(f"已积累 {facts} 条用户事实")
        if peak_hour >= 0:
            parts.append(f"活跃高峰：{peak_hour}:00")
        if top_tool:
            parts.append(f"最常用工具：{top_tool}")

        return " | ".join(parts)

    def format_terminal(self, report: dict[str, Any]) -> str:
        """生成完整的终端可读报告（/insights 命令时调用）。"""
        if report.get("empty"):
            return f"📊 最近 {report.get('days', 30)} 天没有会话记录。"

        ov = report.get("overview", {})
        lines = [
            f"📊 Navi 使用洞察（最近 {report['days']} 天）",
            "=" * 40,
            f"  会话数：{ov.get('total_sessions', 0)}",
            f"  总消息：{ov.get('total_messages', 0)}",
            f"  工具调用：{ov.get('total_tool_calls', 0)}",
            f"  平均每次会话消息数：{ov.get('avg_messages_per_session', 0)}",
            f"  用户事实积累：{report.get('facts_count', 0)} 条",
            "",
            "🔧 工具使用频率（前5）：",
        ]

        for t in report.get("tools", [])[:5]:
            bar = "█" * min(20, t["total"])
            lines.append(
                f"  {t['name']:<20} {t['total']:>4}次  成功率 {t['success_rate']:.0%}  {bar}"
            )

        hourly = report.get("activity", {}).get("hourly", [])
        if hourly and max(hourly) > 0:
            lines.append("")
            lines.append("⏰ 活跃时段分布（每小时）：")
            peak = max(hourly)
            for h, count in enumerate(hourly):
                if count > 0:
                    bar = "█" * max(1, int(count / peak * 15))
                    lines.append(f"  {h:02d}:00  {bar} ({count})")

        return "\n".join(lines)


# ── 数据写入工具（供 loop.py 调用）──────────────────────────────────────────────

def record_tool_call(
    db_path: str,
    session_id: str,
    tool_name: str,
    success: bool = True,
    duration_ms: int | None = None,
) -> None:
    """
    写入一条工具调用记录（非阻塞，失败静默）。
    在 loop.py 每次工具调用完成后调用。
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO tool_call_log (session_id, tool_name, called_at, success, duration_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, tool_name, time.time(), 1 if success else 0, duration_ms),
            )
    except Exception:
        pass  # 日志记录失败不影响主流程


# ── P1-3: Skill 使用统计（InsightsEngine → Skill 反馈闭环）────────────────────

# 用于从 read_file 调用的路径反推 skill 名
# 匹配：.../skills/{name}/SKILL.md（兼容 Win/POSIX 路径分隔符）
import re as _re
_SKILL_PATH_PATTERN = _re.compile(
    r"[/\\]skills[/\\]([^/\\]+)[/\\]SKILL\.md$",
    _re.IGNORECASE,
)


def detect_skill_from_read_path(path: str) -> str | None:
    """
    从 read_file 工具调用的 path 参数反推 skill 名。
    返回 skill 名（如 "github-ops"）或 None。
    """
    if not path:
        return None
    # 兼容传入 dict 形式参数 {"path": "..."} 之类的容错由调用方负责
    m = _SKILL_PATH_PATTERN.search(str(path))
    return m.group(1) if m else None


def record_skill_use(
    db_path: str,
    skill_name: str,
    success: bool = True,
    session_id: str | None = None,
) -> None:
    """
    记录一次 skill 加载（loop.py 在 read_file('SKILL.md') 后调用）。
    非阻塞，失败静默。
    """
    if not skill_name:
        return
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO skill_usage_log (skill_name, used_at, success, session_id)
                   VALUES (?, ?, ?, ?)""",
                (skill_name, time.time(), 1 if success else 0, session_id),
            )
    except Exception:
        pass


def get_skill_usage_stats(
    db_path: str,
    days: int = 7,
) -> dict[str, dict[str, Any]]:
    """
    获取 skill 使用统计（最近 N 天）。

    Returns:
        {
          "skill_name": {
            "count":         int,    # 加载次数
            "fail_rate":     float,  # 失败率 0.0-1.0
            "last_used_at":  float,  # 最后使用 unix timestamp（None 表示从未）
            "days_since":    int,    # 距今天数（None 表示从未使用）
          },
          ...
        }
    无数据时返回空 dict（不会抛异常）。
    """
    cutoff = time.time() - days * 86400
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT skill_name,
                          COUNT(*) AS total,
                          SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures,
                          MAX(used_at) AS last_used_at
                   FROM skill_usage_log
                   WHERE used_at >= ?
                   GROUP BY skill_name""",
                (cutoff,),
            ).fetchall()
    except Exception:
        return {}

    now = time.time()
    result: dict[str, dict[str, Any]] = {}
    for r in rows:
        total = r["total"] or 0
        failures = r["failures"] or 0
        last_used = r["last_used_at"]
        result[r["skill_name"]] = {
            "count": total,
            "fail_rate": round(failures / total, 3) if total else 0.0,
            "last_used_at": last_used,
            "days_since": int((now - last_used) / 86400) if last_used else None,
        }
    return result


def upsert_agent_session(
    db_path: str,
    session_id: str,
    message_count: int = 0,
    tool_call_count: int = 0,
    model: str | None = None,
    ended: bool = False,
) -> None:
    """
    写入或更新 agent_sessions 记录（INSERT OR REPLACE，幂等）。
    在每轮对话结束后调用。
    """
    try:
        now = time.time()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO agent_sessions
                   (id, started_at, ended_at, message_count, tool_call_count, model, source)
                   VALUES (?, ?, ?, ?, ?, ?, 'navi')
                   ON CONFLICT(id) DO UPDATE SET
                     ended_at        = CASE WHEN ? THEN ? ELSE ended_at END,
                     message_count   = excluded.message_count,
                     tool_call_count = excluded.tool_call_count,
                     model           = COALESCE(excluded.model, model)""",
                (session_id, now, now if ended else None,
                 message_count, tool_call_count, model,
                 ended, now),
            )
    except Exception:
        pass


# ── 定期 LLM 深度分析（缺口三·Step2）────────────────────────────────────────

_DEEP_ANALYSIS_PROMPT = """\
以下是 Navi AI 助手最近的工具调用统计数据：

{stats_summary}

请作为 Navi 的自我分析系统，生成一份简短的行为洞察（不超过150字），指出：
1. 哪个工具失败率最高？可能原因是什么？
2. 有什么值得改进的使用模式？
3. 给出一条可操作的改进建议。

直接输出洞察文本，不要任何标题或前缀。"""


async def maybe_run_deep_analysis(db_path: str, llm_chat_fn) -> str | None:
    """
    每 50 次工具调用触发一次 LLM 深度分析（文档 Step2）。
    分析结果缓存到 SQLite insights 表（如果存在），供下次 format_summary 读取。

    Args:
        db_path:      SQLite 路径
        llm_chat_fn:  llm_client.chat 函数

    Returns:
        分析文本（如果本次触发了分析），否则 None
    """
    TRIGGER_COUNT = 50

    try:
        with sqlite3.connect(db_path) as conn:
            # 检查累计工具调用数是否达到触发阈值
            row = conn.execute("SELECT COUNT(*) FROM tool_call_log").fetchone()
            total = row[0] if row else 0
            if total % TRIGGER_COUNT != 0 or total == 0:
                return None

            # 获取最近100条记录做统计
            rows = conn.execute(
                """SELECT tool_name, success FROM tool_call_log
                   ORDER BY called_at DESC LIMIT 100"""
            ).fetchall()
    except Exception:
        return None

    if not rows:
        return None

    # 构建统计摘要
    from collections import Counter
    tool_counts: Counter = Counter()
    fail_counts: Counter = Counter()
    for tool_name, success in rows:
        tool_counts[tool_name] += 1
        if not success:
            fail_counts[tool_name] += 1

    lines = ["工具调用统计（最近100次）："]
    for tool, total_calls in tool_counts.most_common():
        fails = fail_counts.get(tool, 0)
        rate = (total_calls - fails) / total_calls * 100
        lines.append(f"  {tool}: {total_calls}次, 成功率 {rate:.0f}%")
    stats_summary = "\n".join(lines)

    try:
        from llm_constants import MEMORY_CONTRADICTION_MAX_TOKENS, MEMORY_CONTRADICTION_TEMPERATURE
        insights_text = llm_chat_fn(
            messages=[{"role": "user", "content": _DEEP_ANALYSIS_PROMPT.replace("{stats_summary}", stats_summary)}],
            system="你是一个 AI 助手的自我分析模块。生成简洁、可操作的行为洞察。",
            temperature=MEMORY_CONTRADICTION_TEMPERATURE,
            max_tokens=min(MEMORY_CONTRADICTION_MAX_TOKENS, 300),
        )
        return insights_text.strip() if insights_text else None
    except Exception:
        return None


# ── 单例 ─────────────────────────────────────────────────────────────────────

_engine: Optional[InsightsEngine] = None


def get_insights_engine(db_path: str | Path | None = None) -> InsightsEngine | None:
    """获取全局单例 InsightsEngine。首次调用需传入 db_path。"""
    global _engine
    if _engine is None and db_path:
        _engine = InsightsEngine(db_path)
    return _engine
