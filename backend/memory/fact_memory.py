"""
FactMemory · 用户事实知识库

存储用户事实（preference / habit / skill / status / biographical / relationship），
作为 user 实体的唯一 SSOT（Single Source of Truth）。

关键设计：
  - Additive-Only：旧 fact 不删，靠 superseded_by 链做版本演进
  - Hash 去重：避免同一事实被多次抽取
  - 矛盾检测：新 fact 入库前与同类对比，冲突则把旧 fact 标记 superseded
  - 异步 ingestion：抽取走 worker，不阻塞对话主循环

为什么这样做：
  - Additive-Only：保留时间线，便于 debug 和回溯
  - Hash 去重：LLM 抽取本身不稳定，多轮会话会重复输出同一事实
  - 矛盾检测：用户偏好会变（之前喜欢 A，现在喜欢 B），不能两条并存
"""
import asyncio
import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("navi.memory.phase3")


# ══════════════════════════════════════════════════════════════════
#  L3: Fact Memory — 用户事实知识库
# ══════════════════════════════════════════════════════════════════

class FactMemory:
    """
    用户事实记忆。Additive-Only + Hash 去重。
    存储在 SQLite user_facts 表 + ChromaDB（语义检索）。
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._hash_cache: Optional[Set[str]] = None  # 懒加载
        self._ensure_schema()
        logger.info(f"FactMemory 初始化，现有 {self.count()} 条用户事实")

    def _ensure_schema(self):
        """确保 user_facts 表有 superseded_by 列（向后兼容迁移）。"""
        conn = self._get_conn()
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(user_facts)").fetchall()}
            if "superseded_by" not in cols:
                conn.execute("ALTER TABLE user_facts ADD COLUMN superseded_by TEXT")
                conn.commit()
                logger.info("[Fact] 迁移: 添加 superseded_by 列")
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_hash_cache(self) -> Set[str]:
        """加载所有已存在的 content_hash（用于快速去重）。"""
        if self._hash_cache is None:
            conn = self._get_conn()
            try:
                rows = conn.execute("SELECT content_hash FROM user_facts").fetchall()
                self._hash_cache = {r["content_hash"] for r in rows if r["content_hash"]}
            finally:
                conn.close()
        return self._hash_cache

    def add_facts(self, facts: List[Dict]) -> int:
        """
        批量添加 facts（Additive-Only + Hash 去重）。
        facts: [{"content": "...", "category": "...", "confidence": 0.9}, ...]
        返回实际新增的数量。

        写入流程：
          1. Hash 去重（完全重复 → 跳过）
          2. 插入新 fact（立即返回，不阻塞）
          3. 矛盾检测作为后台异步任务入队（不阻塞写入路径）

        矛盾检测异步化原因：
          - _llm_check_contradictions 是同步 LLM 调用，批量写入时会串行调用多次
          - 写入完成后由 _contradiction_worker 在后台异步处理，零影响主流程
        """
        if not facts:
            return 0

        existing_hashes = self._load_hash_cache()
        conn = self._get_conn()
        added = 0
        now = datetime.now().isoformat()
        # 记录本次新增的 (fact_id, content, category) 供后台矛盾检测使用
        newly_added: List[Tuple[str, str, str]] = []

        try:
            for fact in facts:
                content = (fact.get("content") or "").strip()
                if not content or len(content) < 4:
                    continue

                content_hash = hashlib.md5(content.lower().encode()).hexdigest()
                if content_hash in existing_hashes:
                    logger.debug(f"[Fact] Hash 去重跳过: {content[:30]}")
                    continue

                fact_id = str(uuid.uuid4())
                category = fact.get("category", "status")
                confidence = min(max(float(fact.get("confidence", 0.8)), 0.0), 1.0)
                source_session = fact.get("source_session", "")

                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_facts "
                        "(fact_id, content, content_hash, category, source_session, confidence, created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (fact_id, content, content_hash, category, source_session, confidence, now),
                    )
                    existing_hashes.add(content_hash)
                    added += 1
                    newly_added.append((fact_id, content, category))
                except sqlite3.IntegrityError:
                    # content_hash UNIQUE 冲突，跳过
                    pass

            conn.commit()
            if added:
                logger.info(f"[Fact] 新增 {added} 条 facts（去重后）")
        finally:
            conn.close()

        # ── 矛盾检测：写入完成后入队，后台异步处理，不阻塞调用方 ──────────
        if newly_added:
            _enqueue_contradiction_check(self._db_path, newly_added)

        return added

    def get_all_facts(self, limit: int = 100) -> List[Dict]:
        """获取所有有效 facts（排除已被废弃的，用于全量注入 system prompt）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT fact_id, content, category, confidence, created_at "
                "FROM user_facts WHERE superseded_by IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_facts_by_category(self, category: str, limit: int = 20) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT fact_id, content, category, confidence, created_at "
                "FROM user_facts WHERE category=? AND superseded_by IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (category, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def search_facts(self, query: str, limit: int = 5) -> List[Dict]:
        """
        按关键词全文检索最相关的事实（用于 prefetch_all）。
        使用 SQLite LIKE 模糊匹配，简单有效，不需要向量检索。

        Args:
            query: 用户消息原文（取前100字符作为检索关键词）
            limit: 最多返回条数

        Returns:
            匹配的 fact 列表，按 confidence 降序
        """
        # 提取关键词（取前100字，分词用空格切割，过滤短词）
        words = [w for w in query[:100].split() if len(w) >= 2]
        if not words:
            return self.get_recent_facts(limit)

        conn = self._get_conn()
        try:
            # 对每个关键词构建 LIKE 条件，取并集
            conditions = " OR ".join(["content LIKE ?" for _ in words[:5]])
            params = [f"%{w}%" for w in words[:5]]
            params.append(limit)
            rows = conn.execute(
                f"SELECT fact_id, content, category, confidence, created_at "
                f"FROM user_facts WHERE superseded_by IS NULL AND ({conditions}) "
                f"ORDER BY confidence DESC, created_at DESC LIMIT ?",
                params,
            ).fetchall()
            result = [dict(r) for r in rows]
            # 不足 limit 时用最近事实补齐
            if len(result) < limit:
                existing_ids = {r["fact_id"] for r in result}
                fallback = [
                    f for f in self.get_recent_facts(limit)
                    if f["fact_id"] not in existing_ids
                ]
                result.extend(fallback[:limit - len(result)])
            return result
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._get_conn()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM user_facts WHERE superseded_by IS NULL"
            ).fetchone()[0]
        finally:
            conn.close()

    # ── 矛盾检测 ─────────────────────────────────────────────────

    def _detect_contradictions(self, new_content: str, category: str) -> List[str]:
        """
        检测新 fact 是否与同类已有 facts 矛盾。
        返回应被废弃的旧 fact_id 列表。

        策略：
          1. 取同类别的所有活跃 facts（通常 < 30 条）
          2. 单次 LLM 调用判断哪些与新 fact 矛盾
          3. 矛盾的旧 fact → 标记 superseded_by = new_fact_id
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT fact_id, content FROM user_facts "
                "WHERE category=? AND superseded_by IS NULL",
                (category,),
            ).fetchall()
        finally:
            conn.close()

        existing = [dict(r) for r in rows]
        if not existing:
            return []

        # 快速预筛：只取可能相关的（共享至少一个实体词）
        # 避免对每条 fact 都调 LLM
        candidates = []
        new_tokens = set(_extract_entity_tokens(new_content))
        for f in existing:
            old_tokens = set(_extract_entity_tokens(f["content"]))
            # 有交集才可能矛盾（"用户在杭州" vs "用户在广州" 共享 "用户"）
            if new_tokens & old_tokens:
                candidates.append(f)

        if not candidates:
            return []

        # LLM 矛盾判断（批量，一次调用）
        return _llm_check_contradictions(new_content, candidates)

    def get_superseded_facts(self, limit: int = 50) -> List[Dict]:
        """获取已被废弃的 facts（调试/审计用）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT fact_id, content, category, superseded_by, created_at "
                "FROM user_facts WHERE superseded_by IS NOT NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def to_context_text(self) -> str:
        """
        生成给 LLM 的用户画像上下文（仅活跃 facts）。
        Facts < 50 条时全量注入；> 50 条时取最新 30 条。
        """
        total = self.count()
        facts = self.get_all_facts(limit=50 if total <= 50 else 30)
        if not facts:
            return ""
        lines = []
        for f in facts:
            lines.append(f"- {f['content']} ({f['category']})")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  后台矛盾检测任务队列（异步，不阻塞写入路径）
# ══════════════════════════════════════════════════════════════════

# 任务队列：每个元素是 (db_path, [(fact_id, content, category), ...])
_contradiction_queue: "asyncio.Queue | None" = None


def _get_contradiction_queue() -> "asyncio.Queue":
    """懒加载全局矛盾检测任务队列（maxsize=200，防积压）。"""
    global _contradiction_queue
    if _contradiction_queue is None:
        _contradiction_queue = asyncio.Queue(maxsize=200)
    return _contradiction_queue


def _enqueue_contradiction_check(
    db_path: str,
    newly_added: List[Tuple[str, str, str]],
) -> None:
    """
    将矛盾检测任务入队（非阻塞）。
    由 add_facts() 写入完成后调用，不等待结果。
    """
    q = _get_contradiction_queue()
    try:
        q.put_nowait((db_path, newly_added))
    except Exception:
        # 队列满时丢弃，不阻塞写入
        logger.warning("[Fact] 矛盾检测队列已满，跳过本批检测（不影响写入）")


async def _contradiction_worker_loop() -> None:
    """
    后台矛盾检测 Worker（独立异步任务）。
    每次取一个任务，对新写入的每条 fact 执行矛盾检测 + DB 更新。
    在 memory_worker 里由 asyncio.create_task 启动。
    """
    q = _get_contradiction_queue()
    loop = asyncio.get_running_loop()

    while True:
        try:
            db_path, newly_added = await asyncio.wait_for(q.get(), timeout=5.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break

        # 在线程池执行同步 LLM 调用，不阻塞事件循环
        try:
            await loop.run_in_executor(
                None,
                _run_contradiction_checks,
                db_path,
                newly_added,
            )
        except Exception as e:
            logger.warning(f"[Fact] 后台矛盾检测异常: {e}")
        finally:
            q.task_done()


def _run_contradiction_checks(
    db_path: str,
    newly_added: List[Tuple[str, str, str]],
) -> None:
    """
    同步执行矛盾检测（在线程池里跑，不在事件循环线程）。
    对每条新 fact 做 _detect_contradictions，把冲突旧 fact 标记 superseded_by。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    total_superseded = 0

    try:
        for fact_id, content, category in newly_added:
            # 取同类活跃 facts
            rows = conn.execute(
                "SELECT fact_id, content FROM user_facts "
                "WHERE category=? AND superseded_by IS NULL AND fact_id != ?",
                (category, fact_id),
            ).fetchall()
            existing = [dict(r) for r in rows]
            if not existing:
                continue

            # 快速预筛
            new_tokens = set(_extract_entity_tokens(content))
            candidates = [
                f for f in existing
                if new_tokens & set(_extract_entity_tokens(f["content"]))
            ]
            if not candidates:
                continue

            # LLM 矛盾判断
            superseded_ids = _llm_check_contradictions(content, candidates)
            for old_id in superseded_ids:
                conn.execute(
                    "UPDATE user_facts SET superseded_by=? WHERE fact_id=?",
                    (fact_id, old_id),
                )
                total_superseded += 1
                logger.info(
                    f"[Fact] 后台矛盾调和: '{content[:25]}' 废弃了 fact_id={old_id[:8]}"
                )

        if total_superseded:
            conn.commit()
            logger.info(f"[Fact] 后台矛盾检测完成，废弃 {total_superseded} 条旧 facts")

    except Exception as e:
        logger.warning(f"[Fact] _run_contradiction_checks 异常: {e}")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
#  矛盾检测工具函数
# ══════════════════════════════════════════════════════════════════

import re as _re

def _extract_entity_tokens(text: str) -> List[str]:
    """
    从 fact 文本中提取实体词（用于快速预筛）。
    策略：提取所有 2-4 字的中文片段 + 英文单词，过滤停用词。
    """
    _STOP = {"用户", "喜欢", "正在", "目前", "之前", "现在", "曾经", "已经",
             "可能", "应该", "一直", "经常", "偶尔", "非常", "比较", "一般"}
    tokens = []
    # 中文：提取连续中文 2-4 字片段
    for seg in _re.findall(r'[\u4e00-\u9fff]{2,}', text):
        if seg not in _STOP:
            tokens.append(seg)
    # 英文：提取单词
    for word in _re.findall(r'[a-zA-Z]{2,}', text):
        tokens.append(word.lower())
    return tokens


# ── 辩证调和 Prompt（三步推理：观察 → 分析 → 调和）────────────────────────────
_DIALECTIC_CONTRADICTION_PROMPT = """\
你在帮助构建一个用户的长期画像（User Model）。

新观察到的事实：
{new_fact}

同类别的已有事实：
{existing_facts}

请按以下三步分析：

【第一步：观察】新事实和已有事实分别描述了什么？

【第二步：分析】新事实与哪些已有事实存在关系？
- 直接矛盾（不可能同时为真，如"在杭州" vs "在广州"）
- 状态更新（用户情况改变，如"正在学Python" → "正在学Go"）
- 内容补充（两者兼容，新增细节）
- 无关（完全不同维度）

【第三步：调和】对每条存在矛盾或更新关系的已有事实，判断处理方式：
- update：新信息替代旧信息（习惯/状态已改变）
- coexist：两者并存（不同时间或上下文下均成立）

输出格式（严格 JSON，不要输出任何其他内容）：
{{
  "reasoning": "简短说明调和逻辑",
  "supersede_indices": [0, 2]
}}

supersede_indices：应被新事实替代的已有事实编号列表（从0开始）。
如果没有需要替代的，输出空数组 []。"""


def _llm_check_contradictions(new_content: str, candidates: List[Dict]) -> List[str]:
    """
    调用 LLM 辩证调和新 fact 与候选旧 facts 的关系（三步推理）。
    返回应被废弃的旧 fact_id 列表。
    失败时安全降级，不阻塞写入。
    """
    from llm_client import chat

    existing_lines = []
    for i, c in enumerate(candidates):
        existing_lines.append(f"[{i}] {c['content']}")

    prompt = _DIALECTIC_CONTRADICTION_PROMPT.replace(
        "{new_fact}", new_content
    ).replace(
        "{existing_facts}", "\n".join(existing_lines)
    )

    try:
        from llm_constants import MEMORY_CONTRADICTION_MAX_TOKENS, MEMORY_CONTRADICTION_TEMPERATURE
        raw = chat(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个精确的用户画像维护者。只输出 JSON，不要输出任何解释。",
            temperature=MEMORY_CONTRADICTION_TEMPERATURE,
            max_tokens=MEMORY_CONTRADICTION_MAX_TOKENS,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        if not isinstance(result, dict):
            return []

        reasoning = result.get("reasoning", "")
        indices = result.get("supersede_indices", [])
        if not isinstance(indices, list):
            return []

        superseded_ids = []
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                superseded_ids.append(candidates[idx]["fact_id"])
                logger.info(
                    f"[Fact] 辩证调和: '{new_content[:25]}' 替代 '{candidates[idx]['content'][:25]}' "
                    f"| 理由: {reasoning[:50]}"
                )
        return superseded_ids

    except Exception as e:
        logger.warning(f"[Fact] 辩证调和 LLM 调用失败: {e}")
        return []  # 失败时不阻塞写入，安全降级


# ══════════════════════════════════════════════════════════════════
#  准入控制（A-MAC 简化版，纯规则）
# ══════════════════════════════════════════════════════════════════

def should_ingest(messages: List[Dict]) -> Tuple[bool, str]:
    """
    准入判断：这次会话是否值得提取记忆？
    基于 A-MAC (ICLR 2026) 的 Type Prior 特征（权重 0.60）简化版。
    """
    user_turns = [m for m in messages if m.get("role") == "user"]

    # 规则 1: 对话轮数太少
    if len(user_turns) < 3:
        return False, "too_few_turns"

    # 规则 2: 对话总字数太少
    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars < 100:
        return False, "too_short"

    # 规则 3: 纯工具调用会话
    meaningful = [m for m in user_turns if len(m.get("content", "")) > 15]
    if len(meaningful) < 2:
        return False, "tool_only"

    return True, "pass"


# ══════════════════════════════════════════════════════════════════
#  LLM 提取：一次调用同时输出 EventSummary + Facts
# ══════════════════════════════════════════════════════════════════

_EXTRACTION_PROMPT = """\
你是 Navi 的记忆归档系统。请分析以下对话，输出两部分内容。

## Part 1: 事件摘要 (Event Summary)
用 1-3 段自然语言概括这次对话的主题、关键决策和结论。

## Part 2: 用户事实 (User Facts)
从对话中提取关于用户的**客观事实**。规则：
1. 只提取用户明确表达的信息，不推测
2. 每条 fact 必须是原子级的（一条只描述一个事实）
3. 分类：preference（偏好）/ status（当前状态）/ relationship（人际关系）/ skill（技能）/ habit（习惯）/ biographical（个人信息）
4. 只提取有长期价值的信息（"我今天很累"不算，"我在杭州工作"算）

## 对话记录
{conversation}

## 输出（严格 JSON，不要输出任何其他内容）
{{
  "event": {{
    "title": "一句话标题",
    "summary": "1-3 段摘要",
    "entities": ["实体1", "实体2"],
    "topics": ["主题1", "主题2"]
  }},
  "facts": [
    {{"content": "事实内容", "category": "分类", "confidence": 0.9}}
  ]
}}

如果对话中没有值得提取的用户事实，facts 返回空数组 []。"""


def _format_conversation(messages: List[Dict]) -> str:
    """将消息列表格式化为对话文本。"""
    lines = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if role == "user":
            lines.append(f"用户: {content}")
        elif role == "assistant":
            # 截断长回复，避免浪费 token
            lines.append(f"Navi: {content[:500]}")
    return "\n".join(lines)


def extract_memory(messages: List[Dict]) -> Optional[Dict[str, Any]]:
    """
    调用 LLM 从对话中提取 EventSummary + Facts。
    返回 {"event": {...}, "facts": [...]} 或 None。
    """
    from llm_client import chat

    conversation_text = _format_conversation(messages)
    prompt = _EXTRACTION_PROMPT.replace("{conversation}", conversation_text)

    try:
        from llm_constants import MEMORY_EXTRACTION_MAX_TOKENS, MEMORY_EXTRACTION_TEMPERATURE
        raw = chat(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个精确的信息提取系统。只输出 JSON，不要输出任何解释。",
            temperature=MEMORY_EXTRACTION_TEMPERATURE,
            max_tokens=MEMORY_EXTRACTION_MAX_TOKENS,
        )

        # 尝试从回复中提取 JSON
        raw = raw.strip()
        # 处理可能的 markdown 代码块包裹
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)

        # 基本校验
        if "event" not in result:
            result["event"] = {"title": "对话", "summary": "", "entities": [], "topics": []}
        if "facts" not in result:
            result["facts"] = []

        logger.info(
            f"[Extract] 提取完成: event='{result['event'].get('title', '')[:30]}', "
            f"facts={len(result['facts'])} 条"
        )
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"[Extract] JSON 解析失败: {e}, raw={raw[:200]}")
        return None
    except Exception as e:
        logger.error(f"[Extract] LLM 调用失败: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  异步任务队列 + Worker
# ══════════════════════════════════════════════════════════════════

def enqueue_memory_job(session_id: str, db_path: str):
    """将一个会话加入记忆提取队列（WS 断开时调用，< 1ms）。"""
    conn = sqlite3.connect(db_path)
    try:
        now = datetime.now().isoformat()
        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT OR IGNORE INTO memory_jobs (job_id, session_id, status, created_at) "
            "VALUES (?,?,?,?)",
            (job_id, session_id, "pending", now),
        )
        conn.commit()
        logger.info(f"[MemJob] 排队: session={session_id[:8]}...")
    except Exception as e:
        logger.debug(f"[MemJob] 排队失败（可能重复）: {e}")
    finally:
        conn.close()


def _get_session_messages(session_id: str, db_path: str) -> List[Dict]:
    """从 chat_messages 表获取会话的所有消息。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _process_single_job(job: Dict, db_path: str, fact_mem: FactMemory):
    """处理单个记忆提取任务（同步，在 Worker 线程中运行）。"""
    session_id = job["session_id"]
    job_id = job["job_id"]

    conn = sqlite3.connect(db_path)
    try:
        # 标记为 processing
        conn.execute(
            "UPDATE memory_jobs SET status='processing' WHERE job_id=?", (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    try:
        # 获取会话消息
        messages = _get_session_messages(session_id, db_path)

        # 准入控制
        should, reason = should_ingest(messages)
        if not should:
            logger.info(f"[MemJob] 跳过 session={session_id[:8]}... 原因={reason}")
            _update_job_status(job_id, "done", db_path, error=f"skipped:{reason}")
            return

        # LLM 提取
        result = extract_memory(messages)
        if not result:
            _update_job_status(job_id, "failed", db_path, error="extraction_failed")
            return

        # 持久化 L2 EventSummary
        event = result.get("event", {})
        if event.get("summary"):
            try:
                from memory.episodic_memory import get_episodic_memory
                em = get_episodic_memory()
                text = f"{event.get('title', '')}. {event.get('summary', '')}"
                entities = event.get("entities", [])
                topics = event.get("topics", [])
                em.add(
                    text=text,
                    doc_type="event_summary",
                    doc_date=datetime.now().strftime("%Y-%m-%d"),
                    metadata={
                        "session_id": session_id,
                        "entities": ",".join(entities),
                        "topics": ",".join(topics),
                    },
                    doc_id=f"event_{session_id}",
                )
                logger.info(f"[MemJob] L2 EventSummary 写入: '{event.get('title', '')[:30]}'")
            except Exception as e:
                logger.warning(f"[MemJob] L2 写入失败: {e}")

        # 持久化 L3 Facts
        facts = result.get("facts", [])
        if facts:
            for f in facts:
                f["source_session"] = session_id
            added = fact_mem.add_facts(facts)
            logger.info(f"[MemJob] L3 Facts 写入: {added} 条新增")

        _update_job_status(job_id, "done", db_path)

    except Exception as e:
        logger.error(f"[MemJob] 处理失败 session={session_id[:8]}...: {e}")
        _update_job_status(job_id, "failed", db_path, error=str(e)[:500])


def _update_job_status(job_id: str, status: str, db_path: str, error: Optional[str] = None):
    conn = sqlite3.connect(db_path)
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE memory_jobs SET status=?, processed_at=?, error=? WHERE job_id=?",
            (status, now, error, job_id),
        )
        conn.commit()
    finally:
        conn.close()


async def memory_worker(db_path: str, interval: int = 30):
    """
    后台 Worker：定时扫描 memory_jobs 表，处理 pending 任务。
    在 main.py 的 lifespan 中作为 asyncio.Task 启动。
    """
    logger.info("[MemWorker] 记忆提取 Worker 启动")
    fact_mem = FactMemory(db_path)

    # 启动时重置 stuck 的 processing 任务
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE memory_jobs SET status='pending' WHERE status='processing'")
        conn.commit()
    finally:
        conn.close()

    while True:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                jobs = conn.execute(
                    "SELECT job_id, session_id FROM memory_jobs "
                    "WHERE status='pending' ORDER BY created_at LIMIT 3"
                ).fetchall()
            finally:
                conn.close()

            for job in jobs:
                job_dict = dict(job)
                # 在线程池中同步执行（避免阻塞事件循环）
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, _process_single_job, job_dict, db_path, fact_mem
                )

        except Exception as e:
            logger.error(f"[MemWorker] Worker 异常: {e}")

        await asyncio.sleep(interval)


# ══════════════════════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════════════════════

_fact_memory: Optional[FactMemory] = None


def get_fact_memory() -> FactMemory:
    global _fact_memory
    if _fact_memory is None:
        from config import get_config
        cfg = get_config()
        db_path = str(Path(cfg.data_dir) / "app.db")
        _fact_memory = FactMemory(db_path)
    return _fact_memory
