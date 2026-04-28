"""
llm_constants.py — 全局 LLM 调用参数常量

所有 backend/ 模块中涉及 max_tokens / temperature 的硬编码值统一在此管理。
修改本文件即可调整整个后端的 token 预算和生成策略，无需翻找各模块。

命名规则：{模块}_{用途}_MAX_TOKENS / {模块}_{用途}_TEMPERATURE

────────────────────────────────────────────────────────────────────
  模块                    用途                  MAX_TOKENS   TEMP
────────────────────────────────────────────────────────────────────
  llm_client             通用文本对话             2048       0.7
  llm_client             单图 Vision              300       -
  llm_client             多图 Vision batch         600       -
  collector              活动分类（按活动数动态）   ~200+      -
  collector              截图理解                  500       -
  memory                 事实矛盾检测              256       0.0
  memory                 对话提取事实              4096       0.1
  report                 日报生成                 10000       -
  report                 截图批量描述               ~600+     -
  soul.analyzer          素材分析（文本）           4096       0.3
  soul.analyzer          素材分析（图片）           4096       -
  soul.synthesizer       首次合成 SOUL             8192       0.5
  soul.synthesizer       增量修改 SOUL             8192       0.4
  agent.navi_provider    Agent 对话默认            4096       0.7
  agent.loop             Agent 上下文窗口         65536       -
  utils.evaluator        回复质量评估               256       0.0
  proactive.generator    主动对话消息               150       0.8
────────────────────────────────────────────────────────────────────
"""

# ═══════════════════════════════════════════════════════════════════════
#  llm_client.py — 通用 LLM 调用
# ═══════════════════════════════════════════════════════════════════════

# chat() 通用文本对话
LLM_CHAT_MAX_TOKENS = 2048
LLM_CHAT_TEMPERATURE = 0.7

# vision_chat() 单图理解
LLM_VISION_MAX_TOKENS = 300

# vision_batch() 多图批量
LLM_VISION_BATCH_MAX_TOKENS = 600


# ═══════════════════════════════════════════════════════════════════════
#  collector/llm_classifier.py — 活动分类 & 截图理解
# ═══════════════════════════════════════════════════════════════════════

# 活动分类：基础 token（实际 = max(此值, 活动数 * 40)）
# 注意：思维链模型（如 MiniMax-M2.5）的 <think> 块会消耗大量 token，
# 基础值需足够大以保证 JSON 答案有输出余量。
# 20 条活动 JSON ≈ 1100 token，<think> ≈ 600-800 token，总计需 ~2000
CLASSIFIER_BASE_MAX_TOKENS = 2048

# 活动分类：每条活动的 token 预算乘数
CLASSIFIER_PER_ACTIVITY_TOKENS = 40

# 截图理解（vision_chat 调用）
CLASSIFIER_SCREENSHOT_MAX_TOKENS = 500

# gap_detector — 日报追问判断（单次调用，需为思维链模型留余量）
GAP_DETECTOR_MAX_TOKENS = 512


# ═══════════════════════════════════════════════════════════════════════
#  memory/fact_memory.py — 记忆提取
# ═══════════════════════════════════════════════════════════════════════

# 事实矛盾检测
MEMORY_CONTRADICTION_MAX_TOKENS = 256
MEMORY_CONTRADICTION_TEMPERATURE = 0.0

# 对话→事实提取
MEMORY_EXTRACTION_MAX_TOKENS = 4096
MEMORY_EXTRACTION_TEMPERATURE = 0.1


# ═══════════════════════════════════════════════════════════════════════
#  report/report_generator.py — 日报生成
# ═══════════════════════════════════════════════════════════════════════

# 截图筛选数量
REPORT_TOP_N_SCREENSHOTS = 20

# 每次 Vision 批量调用图片数
REPORT_VISION_BATCH_SIZE = 5

# 每张截图描述最大字数
REPORT_MAX_DESC_LEN = 50

# 日报输出 token 上限（含模型思考链，实际正文约 3000-5000 字）
REPORT_MAX_TOKENS = 20000

# 截图批量描述：基础 token（实际 = max(600, 截图数 * (MAX_DESC_LEN + 20))）
REPORT_VISION_DESC_BASE_TOKENS = 600

# 截图批量描述：每张截图的 token 预算
REPORT_VISION_DESC_PER_SHOT = 70   # MAX_DESC_LEN(50) + 20


# ═══════════════════════════════════════════════════════════════════════
#  soul/ — 灵魂合成 & 分析
# ═══════════════════════════════════════════════════════════════════════

# 素材分析（文本类）
SOUL_ANALYZER_TEXT_MAX_TOKENS = 4096
SOUL_ANALYZER_TEXT_TEMPERATURE = 0.3

# 素材分析（图片类）
SOUL_ANALYZER_IMAGE_MAX_TOKENS = 4096

# 首次合成 SOUL.md
SOUL_SYNTHESIZER_MAX_TOKENS = 8192
SOUL_SYNTHESIZER_TEMPERATURE = 0.5

# 增量修改 SOUL.md（用户反馈后微调）
SOUL_REFINE_MAX_TOKENS = 8192
SOUL_REFINE_TEMPERATURE = 0.4


# ═══════════════════════════════════════════════════════════════════════
#  agent/ — Agent 对话
# ═══════════════════════════════════════════════════════════════════════

# NaviLLMProvider 默认参数
AGENT_DEFAULT_MAX_TOKENS = 4096
AGENT_DEFAULT_TEMPERATURE = 0.7

# AgentLoop 上下文窗口（token 计算用，不是生成 max_tokens）
AGENT_CONTEXT_WINDOW_TOKENS = 65_536


# ═══════════════════════════════════════════════════════════════════════
#  utils/evaluator.py — 回复质量评估
# ═══════════════════════════════════════════════════════════════════════

EVALUATOR_MAX_TOKENS = 256
EVALUATOR_TEMPERATURE = 0.0


# ═══════════════════════════════════════════════════════════════════════
#  proactive/ — 主动对话
# ═══════════════════════════════════════════════════════════════════════

PROACTIVE_GENERATOR_MAX_TOKENS = 150
PROACTIVE_GENERATOR_TEMPERATURE = 0.8
