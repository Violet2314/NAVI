"""
WorkingMemory - 当前会话上下文
纯内存，进程重启后清空
存储今日采集到的实时活动，供 Agent 构建上下文用
"""
from datetime import datetime
from typing import List, Dict, Optional
from collections import deque
import threading


class WorkingMemory:
    """
    轻量级内存上下文
    - 自动维护最近 N 条活动
    - 线程安全（采集线程写，API 线程读）
    """

    def __init__(self, max_size: int = 200):
        self._lock = threading.Lock()
        self._activities: deque = deque(maxlen=max_size)
        self._session_start = datetime.now()
        self._current_focus: Optional[str] = None  # 当前正在做什么

    # ---- 写入 ----

    def add_activity(self, activity: Dict):
        """
        采集层调用：新增一条活动记录
        activity = {
            process_name, window_title, started_at,
            duration_sec, app_category, sentiment
        }
        """
        with self._lock:
            self._activities.append({
                **activity,
                "recorded_at": datetime.now().isoformat(),
            })
            self._current_focus = activity.get("window_title", "")

    def set_current_focus(self, process: str, title: str, category: str):
        """窗口采集每次触发时更新当前焦点"""
        with self._lock:
            self._current_focus = f"{process} | {title}"

    # ---- 读取 ----

    def get_recent(self, n: int = 10) -> List[Dict]:
        """获取最近 n 条活动"""
        with self._lock:
            return list(self._activities)[-n:]

    def get_current_focus(self) -> Optional[str]:
        with self._lock:
            return self._current_focus

    def get_session_summary(self) -> Dict:
        """
        今日采集摘要，供日报 prompt 用。
        v3: 优先从 category_breakdown 累加各分类时长（更精确）。
        """
        with self._lock:
            activities = list(self._activities)

        if not activities:
            return {"total": 0, "duration_min": 0, "top_apps": [], "categories": {}}

        category_sec: Dict[str, int] = {}
        app_sec: Dict[str, int] = {}

        for a in activities:
            proc = a.get("process_name", "unknown").replace(".exe", "")
            dur = a.get("duration_sec", 0) or 0
            app_sec[proc] = app_sec.get(proc, 0) + dur

            # 优先从 breakdown 累加（更精确），回退到单一 app_category
            breakdown = a.get("category_breakdown")
            if breakdown:
                for cat, sec in breakdown.items():
                    category_sec[cat] = category_sec.get(cat, 0) + sec
            else:
                cat = a.get("app_category", "other")
                category_sec[cat] = category_sec.get(cat, 0) + dur

        top_apps = sorted(app_sec.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "total": len(activities),
            "session_start": self._session_start.isoformat(),
            "category_minutes": {k: round(v / 60) for k, v in category_sec.items()},
            "top_apps": [{"app": k, "minutes": round(v / 60)} for k, v in top_apps],
        }

    def to_context_text(self, n: int = 10) -> str:
        """
        生成给 LLM 的自然语言上下文（v3: 包含分类分布摘要）
        
        输出示例：
          18:00-18:03 活动段: 工作(Code) 30s + 娱乐(Edge) 2m | 主要=娱乐
        """
        recent = self.get_recent(n)
        if not recent:
            return "暂无最近活动记录。"

        lines = ["最近活动（从旧到新）："]
        CAT_LABELS = {"work": "工作", "learning": "学习", "entertainment": "娱乐",
                      "utility": "工具", "other": "其他"}

        for a in recent:
            t = a.get("started_at", "")[:16] if a.get("started_at") else ""
            dur = a.get("duration_sec", 0)
            dur_str = f"{dur//60}分钟" if dur >= 60 else f"{dur}秒"
            breakdown = a.get("category_breakdown")

            if breakdown and len(breakdown) > 1:
                # 有多分类分布：输出结构化摘要
                sorted_cats = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
                parts = []
                for cat_key, cat_sec in sorted_cats:
                    cat_label = CAT_LABELS.get(cat_key, cat_key)
                    cat_dur = f"{cat_sec//60}m" if cat_sec >= 60 else f"{cat_sec}s"
                    parts.append(f"{cat_label} {cat_dur}")
                dominant = CAT_LABELS.get(sorted_cats[0][0], sorted_cats[0][0])
                proc = a.get("process_name", "").replace(".exe", "")
                lines.append(f"  {t} [{' + '.join(parts)}] {proc} (总{dur_str}, 主要={dominant})")
            else:
                # 单一分类：简洁输出
                proc = a.get("process_name", "").replace(".exe", "")
                title = a.get("window_title", "")[:40]
                cat = a.get("app_category", "other")
                cat_label = CAT_LABELS.get(cat, cat)
                lines.append(f"  {t} [{cat_label}] {proc} - {title} ({dur_str})")

        return "\n".join(lines)

    def clear(self):
        with self._lock:
            self._activities.clear()
            self._current_focus = None


# 全局单例（main.py 注入后供 Agent 使用）
_working_memory: Optional["WorkingMemory"] = None


def get_working_memory() -> "WorkingMemory":
    global _working_memory
    if _working_memory is None:
        _working_memory = WorkingMemory()
    return _working_memory
