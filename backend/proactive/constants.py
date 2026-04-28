"""
constants.py — 主动对话引擎：全局可调常量

所有 proactive/ 模块的硬编码数字和阈值统一在此管理。
修改后无需改动其他文件，引擎下次循环自动生效。

分区：
  [ENGINE]       引擎运行参数
  [TRIGGER]      触发器阈值 & 紧急度
  [GATE]         准入门控参数
  [BUFFER]       想法缓冲区参数
  [EXPERIENCE]   经验学习参数
  [GENERATOR]    LLM 生成参数
  [DISPATCHER]   分发器参数
"""

# ═══════════════════════════════════════════════════════════════════════
#  ENGINE — 引擎运行参数
# ═══════════════════════════════════════════════════════════════════════

# System 2 巡检间隔（秒）：每隔多久扫一次 DB 做深度分析
PATROL_INTERVAL_SEC = 120

# System 2 首次启动延迟（秒）：等系统稳定后再开始巡检
PATROL_INITIAL_DELAY_SEC = 30

# System 1 事件消费超时（秒）：队列无事件时的等待超时
EVENT_CONSUME_TIMEOUT_SEC = 5.0

# DB 查询最近活动的时间窗口
DB_LOOKBACK_HOURS = 2

# DB 查询最近活动的最大条数
DB_LOOKBACK_LIMIT = 20


# ═══════════════════════════════════════════════════════════════════════
#  TRIGGER — 触发器阈值 & 紧急度
# ═══════════════════════════════════════════════════════════════════════

# 连续工作：触发阈值（分钟）
LONG_WORK_THRESHOLD_MIN = 120

# 连续工作：紧急度 (0~1)
LONG_WORK_URGENCY = 0.7

# 摸鱼：触发阈值（分钟）
SLACKING_THRESHOLD_MIN = 1

# 摸鱼：紧急度
SLACKING_URGENCY = 0.4

# 黑名单命中：紧急度
BLACKLIST_URGENCY = 0.6

# 空闲无活动：触发阈值（分钟）
IDLE_THRESHOLD_MIN = 30

# 空闲无活动：紧急度
IDLE_URGENCY = 0.3

# 深夜关怀：起始小时（24h 制）
LATE_NIGHT_START_HOUR = 23

# 深夜关怀：最近多少秒内有活动才算"还在用"
LATE_NIGHT_RECENT_SEC = 600

# 深夜关怀：紧急度
LATE_NIGHT_URGENCY = 0.6

# 早安问候：时段范围
MORNING_GREET_START_HOUR = 7
MORNING_GREET_END_HOUR = 11

# 早安问候：紧急度
MORNING_GREET_URGENCY = 0.3

# 晚间问候：时段范围
EVENING_GREET_START_HOUR = 18
EVENING_GREET_END_HOUR = 20

# 晚间问候：紧急度
EVENING_GREET_URGENCY = 0.3

# 长时间无互动：触发阈值（小时）
PERIODIC_CHAT_THRESHOLD_HOURS = 6

# 长时间无互动：紧急度
PERIODIC_CHAT_URGENCY = 0.2

# Emotion trigger
EMOTION_NEGATIVE_RATIO_THRESHOLD = 0.6
EMOTION_MIN_RECORDS = 5
EMOTION_SHIFT_URGENCY = 0.6
EMOTION_RECOVERY_URGENCY = 0.3
PRESENCE_ABSENT_THRESHOLD_SEC = 120


# ═══════════════════════════════════════════════════════════════════════
#  GATE — 准入门控参数
# ═══════════════════════════════════════════════════════════════════════

# 全局最小间隔（分钟）：两次主动发言之间至少隔这么久
GATE_MIN_INTERVAL_MIN = 30

# 每日主动发言上限
GATE_MAX_DAILY_PROACTIVE = 8

# 安静时段（24h 制）：这个区间内不主动说话
GATE_QUIET_HOURS_START = 0
GATE_QUIET_HOURS_END = 7

# 紧急度基础阈值：低于此值的触发被直接拒绝
# 降低到 0.2 让早安/晚安/周期闲聊等能触发
GATE_BASE_THRESHOLD = 0.2

# 阈值偏移范围上下限（经验学习调整时的极限值）
GATE_THRESHOLD_OFFSET_MIN = -0.3
GATE_THRESHOLD_OFFSET_MAX = 0.3

# 策略级冷却
STRATEGY_COOLDOWN_GREET_MAX_DAILY = 2         # 早安晚安各一次
STRATEGY_COOLDOWN_REMIND_INTERVAL_MIN = 60     # 提醒至少间隔 1 小时
STRATEGY_COOLDOWN_TEASE_INTERVAL_MIN = 45      # 吐槽不要太频繁
STRATEGY_COOLDOWN_CHAT_MAX_DAILY = 2           # 主动闲聊最多 2 次
STRATEGY_COOLDOWN_CARE_INTERVAL_MIN = 120      # 关怀间隔 2 小时
STRATEGY_COOLDOWN_SHARE_INTERVAL_MIN = 120
STRATEGY_COOLDOWN_FOLLOW_UP_MAX_DAILY = 3
STRATEGY_COOLDOWN_SUMMARIZE_MAX_DAILY = 1

# 静默模式默认时长（小时）：用户说"别烦我"时的默认静默
SILENT_MODE_DEFAULT_HOURS = 2.0


# ═══════════════════════════════════════════════════════════════════════
#  BUFFER — 想法缓冲区参数
# ═══════════════════════════════════════════════════════════════════════

# 合并窗口（秒）：窗口内的多个触发合并为一个
BUFFER_MERGE_WINDOW_SEC = 60

# 缓冲区检查间隔（秒）：_buffer_flusher 的轮询频率
BUFFER_CHECK_INTERVAL_SEC = 10


# ═══════════════════════════════════════════════════════════════════════
#  EXPERIENCE — 经验学习参数
# ═══════════════════════════════════════════════════════════════════════

# 判定为 IGNORE 的超时时间（秒）：主动消息发出后多久没回复算无视
FEEDBACK_IGNORE_TIMEOUT_SEC = 300

# ACCEPT 时的阈值偏移量（负数 = 更容易触发）
FEEDBACK_ACCEPT_DELTA = -0.05

# REJECT 时的阈值偏移量（正数 = 更难触发）
FEEDBACK_REJECT_DELTA = 0.10

# IGNORE 时的阈值偏移量
FEEDBACK_IGNORE_DELTA = 0.05

# REJECT 时进入静默模式的时长（小时）
FEEDBACK_REJECT_SILENT_HOURS = 2.0

# 反馈超时检查间隔（秒）
FEEDBACK_CHECK_INTERVAL_SEC = 60

# 拒绝关键词
REJECT_KEYWORDS = [
    "别烦我", "别吵", "安静", "不需要", "不用了", "闭嘴",
    "别说了", "烦死了", "不要打扰", "shut up", "stop",
    "quiet", "不用你管",
]


# ═══════════════════════════════════════════════════════════════════════
#  GENERATOR — LLM 生成参数
# ═══════════════════════════════════════════════════════════════════════

# 主动消息最大 token 数（要短）— 从全局 llm_constants 读取
from llm_constants import (
    PROACTIVE_GENERATOR_MAX_TOKENS as GENERATOR_MAX_TOKENS,
    PROACTIVE_GENERATOR_TEMPERATURE as GENERATOR_TEMPERATURE,
)


# ═══════════════════════════════════════════════════════════════════════
#  DISPATCHER — 分发器参数
# ═══════════════════════════════════════════════════════════════════════

# 主动消息使用的固定会话 key
PROACTIVE_SESSION_KEY = "local_ws___proactive__"

# 持久化到 DB 的 session_id（与 chat_routes.py 中的 PROACTIVE_SESSION_ID 一致）
PROACTIVE_DB_SESSION_ID = "__proactive__"
