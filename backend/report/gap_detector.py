"""
gap_detector.py - 日报追问模式（P0-2）

让 LLM 评估今日 ≥30 分钟的活动段，找出 1 个最值得追问"具体在做什么"的段。
返回 InfoGap，由 report_generator 决定是否暂停日报、推送学习问题。

设计原则（来自 docs/修复路线图-2026-04-28.md）：
  - 一天只问 1 个 gap，极度克制
  - LLM 判断清晰度，比规则更智能
  - 用便宜模型，单次调用，预算 < ¥0.01
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from llm_constants import GAP_DETECTOR_MAX_TOKENS

logger = logging.getLogger("navi.report.gap")

# 至少 ≥30 分钟的活动段才考虑追问（短的不值得问）
MIN_DURATION_SEC = 30 * 60

# 喂给 LLM 评估的最大候选数（够选了，多了浪费 token）
MAX_CANDIDATES = 8


@dataclass
class InfoGap:
    """日报中的信息缺口（需要追问用户）。"""
    activity_id: Optional[int]
    time_range: str           # "14:00-15:30"
    app_summary: str          # "Chrome - 浏览了一些技术文章"
    duration_min: int
    question: str             # 阿米娅的提问


def _format_for_judgment(activities: List[Dict]) -> str:
    """把候选活动段格式化成 LLM 易读的列表。"""
    lines = []
    for i, a in enumerate(activities):
        started = (a.get("started_at") or "")[11:16]
        ended = (a.get("ended_at") or "")[11:16]
        dur_min = (a.get("duration_sec") or 0) // 60
        proc = a.get("process_name", "").replace(".exe", "")
        title = (a.get("window_title") or "").strip()[:60]
        cat = a.get("app_category", "other")

        # 收集子活动的 process 列表（如果有），帮 LLM 判断"是否标题杂乱"
        children = a.get("children") or []
        child_titles_count = len(set((c.get("window_title") or "").strip() for c in children if c.get("window_title")))

        line = f"[{i}] {started}-{ended} | {proc} | 类别={cat} | 时长={dur_min}min | 标题=「{title}」"
        if child_titles_count > 1:
            line += f" | 子活动标题种类数={child_titles_count}"
        lines.append(line)
    return "\n".join(lines)


_JUDGE_PROMPT_TEMPLATE = """你是日报助手。下面是用户今天 ≥30 分钟的活动段，请找出 1 个最值得追问"具体在做什么/学到了什么"的段。

判断标准：
- ✅ 应该问：浏览器（Chrome/Edge/Firefox）+ 标题杂乱 / 通用工具长时间 / 子活动标题种类多
- ❌ 不要问：IDE（Code/Cursor/IDEA）+ 明确项目名（已经清楚）
- ❌ 不要问：游戏 / 视频播放器 / 聊天工具（明显是娱乐或社交）
- ❌ 不要问：所有段都已经能从 app+title 推断出做什么

只挑 1 个最值得问的。如果没有任何一个值得问，返回 should_ask=false。

提问要求：
- 用"用户"称呼（不是"博士"也不是"你"）
- 自然、口语化、像朋友关心，不要审讯感
- 一句话，不超过 50 字
- 末尾可以加"我想记到日报里"

候选活动段：
{activities_summary}

请只输出 JSON，不要任何解释或代码块标记：
{{"should_ask": true, "index": 0, "question": "用户 14:00-15:30 那段在 Chrome 里看了 90 分钟，能告诉我具体在学什么吗？我想记到日报里。"}}
或
{{"should_ask": false}}
"""


def _parse_llm_response(raw: str) -> Optional[Dict]:
    """从 LLM 原始输出里抽 JSON。容错各种 markdown 代码块包裹。"""
    if not raw:
        return None
    text = raw.strip()
    # 去掉常见的 ```json ... ``` 包裹
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 抓第一个 JSON 对象
    match = re.search(r"\{[\s\S]*?\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception as e:
        logger.warning(f"gap_detector: JSON 解析失败 raw={raw[:120]} err={e}")
        return None


def detect_gap_via_llm(activities: List[Dict], date_str: str) -> Optional[InfoGap]:
    """
    用便宜模型判断今天有没有需要追问的活动段。
    
    返回：
        InfoGap：需要追问的段
        None：没有值得问的，或 LLM 调用失败（降级为不问，不阻塞日报）
    """
    if not activities:
        return None

    # 只看 ≥30 分钟的段
    long_acts = [
        a for a in activities
        if (a.get("duration_sec") or 0) >= MIN_DURATION_SEC
    ]
    if not long_acts:
        logger.info(f"gap_detector: 今日({date_str})无 ≥30min 活动段，跳过追问")
        return None

    # 取最长的 MAX_CANDIDATES 个，避免一次喂太多 token
    long_acts = sorted(long_acts, key=lambda a: a.get("duration_sec") or 0, reverse=True)[:MAX_CANDIDATES]

    summary = _format_for_judgment(long_acts)
    prompt = _JUDGE_PROMPT_TEMPLATE.format(activities_summary=summary)

    try:
        from llm_client import chat
        raw = chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=GAP_DETECTOR_MAX_TOKENS,
        )
    except Exception as e:
        logger.warning(f"gap_detector: LLM 调用失败 {e}，降级为不追问")
        return None

    parsed = _parse_llm_response(raw)
    if not parsed:
        logger.warning(f"gap_detector: 无法解析响应，降级为不追问。raw={raw[:120] if raw else 'empty'}")
        return None

    if not parsed.get("should_ask"):
        logger.info(f"gap_detector: LLM 判断今日({date_str})无需追问")
        return None

    idx = parsed.get("index", 0)
    if not isinstance(idx, int) or idx < 0 or idx >= len(long_acts):
        logger.warning(f"gap_detector: index 越界 idx={idx} len={len(long_acts)}，降级")
        return None

    chosen = long_acts[idx]
    question = (parsed.get("question") or "").strip()
    if not question:
        logger.warning("gap_detector: question 为空，降级为不追问")
        return None

    started_short = (chosen.get("started_at") or "")[11:16]
    ended_short = (chosen.get("ended_at") or "")[11:16]
    proc = chosen.get("process_name", "").replace(".exe", "")
    title = (chosen.get("window_title") or "").strip()[:30]
    dur_min = (chosen.get("duration_sec") or 0) // 60

    gap = InfoGap(
        activity_id=chosen.get("id"),
        time_range=f"{started_short}-{ended_short}",
        app_summary=f"{proc} - {title}",
        duration_min=dur_min,
        question=question,
    )
    logger.info(
        f"gap_detector: 找到 1 个 gap | {gap.time_range} {gap.app_summary} ({dur_min}min) | Q: {question[:50]}"
    )
    return gap
