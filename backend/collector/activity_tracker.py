"""
ActivityTracker - 活动段识别器（v3：单层简化）

核心概念：
  活动段（Segment）：一段连续的活动，由主进程切换触发切段。
  不再维护子活动（SubActivity）和分类 dominance 计算。

切段规则：
  1. 切换主进程 → 结束当前段，开始新段
  2. 同一进程超 30 秒空闲 → 切段
  3. 摄像头判定用户离开 → 切段

段的属性：
  - process_name / window_title：当前进程信息
  - app_category：进程分类
  - duration_sec：段的总时长
  - sentiment：情绪标签
"""
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from collector.idle_detector import IdleDetector
from db.write_queue import DbWriteQueue

logger = logging.getLogger(__name__)


def _wm_set_focus(process: str, title: str, category: str) -> None:
    """同步当前焦点到 WorkingMemory，让 Agent 上下文能看到。失败静默。"""
    try:
        from memory.working_memory import get_working_memory
        get_working_memory().set_current_focus(process, title, category)
    except Exception as e:  # pragma: no cover
        logger.debug(f"WorkingMemory.set_current_focus 失败（不影响采集）: {e}")


def _wm_add_activity(activity: Dict) -> None:
    """把已结束的活动段追加到 WorkingMemory 时间轴。失败静默。"""
    try:
        from memory.working_memory import get_working_memory
        get_working_memory().add_activity(activity)
    except Exception as e:  # pragma: no cover
        logger.debug(f"WorkingMemory.add_activity 失败（不影响采集）: {e}")

# 浏览器进程（需要进一步分析标题）
BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe",
    "browser_broker.exe", "msedge.exe",
}

# 编码类主活动进程（统一小写）
CODING_PROCESS = {
    "code.exe", "cursor.exe", "idea64.exe", "pycharm64.exe",
    "devenv.exe", "sublime_text.exe", "notepad++.exe",
    "windowsterminal.exe", "cmd.exe", "powershell.exe",
}

# 内置默认分类（process_name 统一小写 → category）
DEFAULT_CATEGORY: Dict[str, str] = {
    # 编码/工作
    "code.exe": "work", "cursor.exe": "work", "idea64.exe": "work",
    "pycharm64.exe": "work", "devenv.exe": "work",
    "sublime_text.exe": "work", "notepad++.exe": "work",
    "windowsterminal.exe": "work", "cmd.exe": "work",
    "powershell.exe": "work",
    # 设计
    "figma.exe": "work", "sketch.exe": "work",
    "photoshop.exe": "work", "illustrator.exe": "work",
    # 学习
    "obsidian.exe": "learning", "notion.exe": "learning",
    "anki.exe": "learning",
    # 娱乐
    "bilibili.exe": "entertainment", "steam.exe": "entertainment",
    "epicgameslauncher.exe": "entertainment",
    # 工具
    "explorer.exe": "utility", "taskmgr.exe": "utility",
    "mstsc.exe": "utility",
}

# 浏览器标题：娱乐类关键词（命中 → 立刻切断主活动）
ENTERTAINMENT_KEYWORDS = [
    "bilibili", "哔哩哔哩", "youtube", "抖音", "tiktok",
    "netflix", "爱奇艺", "优酷", "腾讯视频", "twitch",
    "_哔哩哔哩_bilibili", "_哔哩哔哩", "_bilibili",
    "游戏热门视频", "热门视频", "番剧", "动画",
    "直播", "live", "streaming",
    "steam", "epic games",
]

# 浏览器标题：文档/学习类关键词（命中 → 辅助，不切断）
DOCS_KEYWORDS = [
    "stackoverflow", "github", "mdn", "docs", "documentation",
    "tutorial", "runoob", "菜鸟教程", "掘金", "segmentfault",
    "dev.to", "medium", "api", "reference", "react", "python",
    "官方文档", "developer", "blog",
]


def classify_browser_activity(window_title: str) -> str:
    """根据浏览器窗口标题判断活动类型"""
    title_lower = window_title.lower()
    for keyword in ENTERTAINMENT_KEYWORDS:
        if keyword in title_lower:
            return "entertainment"
    for keyword in DOCS_KEYWORDS:
        if keyword in title_lower:
            return "auxiliary"
    return "unknown"


def _classify_process(process: str, title: str, app_rules: Dict) -> tuple:
    """
    对进程分类，返回 (category, classification_source)

    优先级（高→低）：
      1. 用户手动规则（is_user_defined=1）
      2. 内置已知应用规则（DEFAULT_CATEGORY）
      3. LLM 缓存规则（app_rules，is_user_defined=0）
      4. pending → 等待 LLM 分类
    """
    proc_lower = process.lower()
    rule = app_rules.get(proc_lower, {})

    if rule.get("is_user_defined"):
        category = rule.get("category", "other")
        cls_source = "user"
    elif proc_lower in DEFAULT_CATEGORY:
        category = DEFAULT_CATEGORY[proc_lower]
        cls_source = "rule"
    elif rule.get("category"):
        category = rule["category"]
        cls_source = "llm"
    else:
        category = ""
        cls_source = "pending"

    # 浏览器特判
    if proc_lower in {p.lower() for p in BROWSER_PROCESSES}:
        bt = classify_browser_activity(title)
        if bt == "entertainment":
            category = "entertainment"
            cls_source = "rule"
        elif bt == "auxiliary":
            category = "work"
            cls_source = "rule"

    if not category:
        category = "other"

    return category, cls_source


@dataclass
class ActivitySegment:
    """
    活动段（v3 单层）：一段连续的活动。
    不再维护子活动列表，段即最小单位。
    """
    process_name: str
    window_title: str
    app_category: str
    classification_source: str
    started_at: datetime
    device_id: str = "home_pc"
    sentiment: str = "neutral"
    ended_at: Optional[datetime] = None
    db_id: Optional[int] = None

    @property
    def duration_sec(self) -> int:
        if self.ended_at:
            return int((self.ended_at - self.started_at).total_seconds())
        return int((datetime.now() - self.started_at).total_seconds())

    @property
    def category_breakdown(self) -> Dict[str, int]:
        """兼容旧 API：单分类的 breakdown"""
        return {self.app_category: self.duration_sec}


class ActivityTracker:
    """
    活动段跟踪器 v3 - 单层简化

    接收 WindowActivityCapture 的原始数据，维护当前活动段。
    切段规则：
      1. 切换主进程 → 新段
      2. 同一进程超 30 秒空闲 → 切段
      3. 摄像头判定用户离开 → 切段
    """

    # 同一进程空闲超过此时长（秒）则切段
    IDLE_CUTOFF_SEC = 30

    def __init__(self, db_path: str, device_id: str = "home_pc",
                 app_rules: Optional[Dict] = None, capture_interval_sec: float = 60.0,
                 idle_threshold_sec: float = 300.0,
                 lookback_minutes: float = 2.0,
                 category_switch_threshold: float = 1.0):
        self._db_path = db_path
        self._device_id = device_id
        self._app_rules: Dict = app_rules or {}
        self._capture_interval_min = capture_interval_sec / 60.0
        self._current_segment: Optional[ActivitySegment] = None
        self._last_window: Optional[Dict] = None
        # 保留参数兼容旧调用，但不再使用
        self._lookback_minutes = lookback_minutes
        self._category_switch_threshold = category_switch_threshold
        # 异步写入队列
        self._write_queue = DbWriteQueue(db_path)
        # 空闲检测器
        self._idle_detector = IdleDetector(idle_threshold_sec=idle_threshold_sec)
        # 主动对话回调
        from typing import Callable
        self._on_activity_change: Optional[Callable] = None
        # CameraCapture 联动
        self._user_present: bool = True
        # 同一进程空闲计时器
        self._same_process_idle_sec: float = 0.0

    def on_presence_update(self, user_present: bool):
        """CameraCapture 回调：更新用户在离状态"""
        prev = self._user_present
        self._user_present = user_present
        if prev and not user_present and self._current_segment:
            now = datetime.now()
            self._end_current_segment(now)
            logger.info(f"📹 摄像头判定用户离开，活动段在 {now.strftime('%H:%M:%S')} 截断")

    def on_emotion_update(self, emotion: str):
        """CameraCapture 回调：更新当前活动段的 sentiment"""
        if self._current_segment:
            self._current_segment.sentiment = emotion

    def update_rules(self, app_rules: Dict):
        """更新 app 规则"""
        self._app_rules = app_rules

    def update_config(self, collector_cfg: Dict):
        """热重载采集器参数"""
        if "idle_threshold_sec" in collector_cfg:
            new_sec = float(collector_cfg["idle_threshold_sec"])
            self._idle_detector.threshold = new_sec
            logger.info(f"[热重载] idle_threshold_sec → {new_sec}s")
        if "window_interval_sec" in collector_cfg:
            self._capture_interval_min = float(collector_cfg["window_interval_sec"]) / 60.0
            logger.info(f"[热重载] capture_interval_min → {self._capture_interval_min:.2f}min")

    # ── 兼容旧接口 ──────────────────────────────────────────────────────────

    @property
    def _current_session(self):
        """兼容旧代码引用 _current_session 的地方"""
        return self._current_segment

    # ── 核心采集逻辑 ────────────────────────────────────────────────────────

    def on_window_captured(self, window_info: Dict):
        """处理一次窗口采集结果"""
        now = datetime.now()
        process = window_info["process_name"]
        title = window_info["window_title"]

        logger.info(f"[采集] {process} | {title[:60]}")

        # ── 空闲检测 ────────────────────────────────────────────────────────
        is_idle, idle_sec = self._idle_detector.check()
        if is_idle:
            if self._user_present:
                # 键鼠空闲但摄像头检测到人 → 不截断
                return
            else:
                if self._current_segment:
                    idle_started_at = max(
                        now - timedelta(seconds=idle_sec),
                        self._current_segment.started_at,
                    )
                    self._end_current_segment(idle_started_at)
                    logger.info(
                        f"IdleDetector: 活动段在 {idle_started_at.strftime('%H:%M:%S')} "
                        f"因空闲截断（键鼠空闲 {idle_sec:.0f}s + 摄像头未检测到人）"
                    )
                return

        # 对当前窗口进行分类
        new_category, new_cls_source = _classify_process(process, title, self._app_rules)

        # ── 第一次采集 / 空闲恢复后开新段 ───────────────────────────────────
        if self._current_segment is None:
            self._start_new_segment(process, title, new_category, new_cls_source, now)
            self._last_window = window_info
            self._same_process_idle_sec = 0.0
            return

        # ── 相同进程 → 更新标题，检查空闲切段 ──────────────────────────────
        if process.lower() == self._current_segment.process_name.lower():
            self._current_segment.window_title = title
            self._same_process_idle_sec = 0.0
            self._last_window = window_info
            # 标题变化也同步给 Agent（例如换浏览器 tab、换文件）
            _wm_set_focus(process, title, self._current_segment.app_category)
            return

        # ── 不同进程 → 切段 ─────────────────────────────────────────────────
        self._end_current_segment(now)
        self._start_new_segment(process, title, new_category, new_cls_source, now)
        self._same_process_idle_sec = 0.0
        self._last_window = window_info

    def get_current_segment(self) -> Optional[Dict]:
        """返回当前正在进行的活动段（兼容旧 API）"""
        if not self._current_segment:
            return None
        seg = self._current_segment
        return {
            "process_name": seg.process_name,
            "window_title": seg.window_title,
            "started_at": seg.started_at.isoformat(),
            "ended_at": None,
            "duration_sec": seg.duration_sec,
            "app_category": seg.app_category,
            "category_breakdown": seg.category_breakdown,
            "sentiment": seg.sentiment,
            "is_current": True,
            "children": [],  # v3 无子活动
        }

    def flush(self):
        """强制结束当前段并写入"""
        if self._current_segment:
            self._end_current_segment(datetime.now())
        self._write_queue.flush()

    def close(self):
        """关闭写线程"""
        self._write_queue.close()

    # ── 段管理 ──────────────────────────────────────────────────────────────

    def _start_new_segment(self, process: str, title: str,
                           category: str, cls_source: str, now: datetime):
        """开始一个新的活动段"""
        self._current_segment = ActivitySegment(
            process_name=process,
            window_title=title,
            app_category=category,
            classification_source=cls_source,
            started_at=now,
            device_id=self._device_id,
        )
        logger.debug(f"新活动段开始：{process} → {category} (source={cls_source})")
        # 同步给 Agent 上下文：让 wm.get_current_focus() 立刻能拿到
        _wm_set_focus(process, title, category)

    def _end_current_segment(self, ended_at: datetime):
        """结束当前活动段，写入 DB"""
        if not self._current_segment:
            return
        seg = self._current_segment
        seg.ended_at = ended_at
        dur = seg.duration_sec

        # 过滤时长 < 30 秒的段
        if dur < 30:
            logger.debug("活动段 < 30s，丢弃")
            self._current_segment = None
            return

        # 写入 DB
        parent_id = self._save_segment_to_db(seg, dur)

        logger.info(
            f"活动段结束：{seg.process_name} "
            f"({dur // 60}分{dur % 60}秒)"
        )

        # 同步到 WorkingMemory，让 Agent 能在 <current-activity> 里看到时间轴
        _wm_add_activity({
            "process_name": seg.process_name,
            "window_title": seg.window_title,
            "started_at": seg.started_at.isoformat(),
            "ended_at": seg.ended_at.isoformat() if seg.ended_at else None,
            "duration_sec": dur,
            "app_category": seg.app_category,
            "category_breakdown": seg.category_breakdown,
            "sentiment": seg.sentiment,
        })

        # 通知主动对话引擎
        if self._on_activity_change:
            try:
                self._on_activity_change({
                    "process_name": seg.process_name,
                    "window_title": seg.window_title,
                    "app_category": seg.app_category,
                    "category_breakdown": seg.category_breakdown,
                    "duration_sec": dur,
                    "started_at": seg.started_at.isoformat(),
                    "ended_at": seg.ended_at.isoformat() if seg.ended_at else None,
                })
            except Exception as e:
                logger.debug(f"主动对话回调失败（不影响采集）: {e}")

        self._current_segment = None

    # ── DB 写入 ─────────────────────────────────────────────────────────────

    def _save_segment_to_db(self, seg: ActivitySegment, total_duration: int) -> Optional[int]:
        """写入活动段到 DB"""
        try:
            conn = sqlite3.connect(self._db_path)
            cur = conn.execute(
                "INSERT INTO window_activities"
                " (started_at, ended_at, duration_sec, process_name, window_title,"
                "  app_category, classification_source, sentiment, device_id,"
                "  is_synced, parent_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)",
                (
                    seg.started_at.isoformat(),
                    seg.ended_at.isoformat() if seg.ended_at else None,
                    total_duration,
                    seg.process_name,
                    seg.window_title,
                    seg.app_category,
                    seg.classification_source,
                    seg.sentiment,
                    seg.device_id,
                ),
            )
            parent_id = cur.lastrowid
            conn.commit()
            conn.close()
            return parent_id
        except Exception as e:
            logger.error(f"活动段写入 DB 失败: {e}")
            return None

    # ── 兼容旧方法（空实现，保持外部调用不报错）────────────────────────────

    def update_segment_params(self, lookback_minutes: Optional[float] = None,
                               category_switch_threshold: Optional[float] = None):
        """[DEPRECATED] v3 不再使用分类 dominance 切段，保留兼容"""
        pass