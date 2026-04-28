"""
EpisodicMemory - 情节记忆（Phase 1+2 升级版）
存储有意义的历史事件（活动段 + 日记片段 + 日报摘要）
支持语义检索：「我上个月学了什么」「我最近在做什么项目」

Phase 1: 阈值门控 + 意图过滤 + 单例修复
Phase 2: Multi-Signal 混合检索（语义 + BM25 关键词 + 时间衰减）
  灵感来源：mem0 (2026.4 算法) + MemoryOS (EMNLP 2025)
"""
import logging
import math
import re
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import chromadb
from chromadb.config import Settings

logger = logging.getLogger("navi.memory.episodic")

# ── 常量 ──────────────────────────────────────────────────────────
SEMANTIC_THRESHOLD = 0.35       # 语义相似度门控：低于此值丢弃（0.50 过于激进，大量有效日记被误杀）

# BM25 归一化 sigmoid 参数（统一一组，有 AB 数据再分档）
BM25_SIGMOID_MIDPOINT = 5.0
BM25_SIGMOID_STEEPNESS = 0.6

# 时间衰减：统一 30 天半衰期 + 180 天陈旧惩罚（先统一，有数据再按类型分档）
RECENCY_HALF_LIFE_DAYS = 30
RECENCY_STALE_DAYS = 180        # 超过 180 天视为"陈旧"，分数再乘以 0.5
SIGNAL_WEIGHTS = {              # 多信号权重（recency 提升，借鉴 SCG-MEM）
    "semantic": 0.50,
    "bm25": 0.30,
    "recency": 0.20,
}
FINAL_SCORE_THRESHOLD = 0.30    # 综合分数门控：低于此值不注入给 LLM

# ── 意图过滤：这些消息不需要检索记忆 ─────────────────────────────
_SKIP_PATTERNS = re.compile(
    r"^(好的?|ok|嗯|谢谢|继续|知道了|是的?|对|没错|行|可以|哈哈|"
    r"不是|不要|不用|取消|算了|停|退出|再见|晚安|早安|你好|hello|hi|hey|"
    r"yes|no|sure|thanks|thx|ok|lol|haha)[\s!！。.？?]*$",
    re.IGNORECASE
)
_MIN_QUERY_LEN = 6  # 少于 6 个字符的查询跳过 RAG


def should_retrieve(query: str) -> bool:
    """Phase 1：判断是否需要检索记忆。闲聊/短消息 → 跳过。"""
    q = query.strip()
    if len(q) < _MIN_QUERY_LEN:
        return False
    if _SKIP_PATTERNS.match(q):
        return False
    return True


# ── BM25 工具函数 ─────────────────────────────────────────────────

def _tokenize_for_bm25(text: str) -> List[str]:
    """简易中英文分词：英文按空格/标点切，中文按单字切。"""
    # 去掉标点、特殊字符
    text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text.lower())
    tokens = []
    for segment in text.split():
        # 如果包含中文字符，按字切分
        if re.search(r'[\u4e00-\u9fff]', segment):
            tokens.extend(list(segment))
        else:
            tokens.append(segment)
    return [t for t in tokens if len(t.strip()) > 0]


def _get_bm25_sigmoid_params(query: str) -> Tuple[float, float]:
    """返回统一的 sigmoid 参数。预留接口，有 AB 数据后可按 query 长度分档。"""
    return BM25_SIGMOID_MIDPOINT, BM25_SIGMOID_STEEPNESS


def _normalize_bm25(raw_score: float, midpoint: float, steepness: float) -> float:
    """将 BM25 原始分数归一化到 [0, 1]（logistic sigmoid）。"""
    return 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))


def _recency_score(doc_date: str, doc_type: str = "default") -> float:
    """
    时间衰减分数：越新越高，指数衰减。
    - 统一 30 天半衰期（简化版，有数据后再按类型分档）
    - 超过 RECENCY_STALE_DAYS 额外降权 50%（Memora 启发：惩罚陈旧记忆）
    """
    half_life = RECENCY_HALF_LIFE_DAYS
    try:
        d = date.fromisoformat(doc_date)
        days_ago = (date.today() - d).days
        if days_ago < 0:
            days_ago = 0
        score = math.exp(-0.693 * days_ago / half_life)  # ln(2) ≈ 0.693
        # Memora 式硬性降权：超过 180 天的陈旧记忆再打 5 折
        if days_ago > RECENCY_STALE_DAYS:
            score *= 0.5
        return score
    except (ValueError, TypeError):
        return 0.3  # 无法解析日期时给中等分数


class EpisodicMemory:
    """
    情节记忆 - 基于 ChromaDB 向量检索 + SQLite FTS5 关键词检索

    三类文档：
      - activity: 采集到的活动段（每天结束后批量写入）
      - diary: 从 Obsidian 导入的历史日记
      - report: 每日生成的日报摘要

    检索策略（Phase 2）：
      final_score = w_semantic * semantic + w_bm25 * bm25_norm + w_recency * recency
      其中 semantic < SEMANTIC_THRESHOLD 的候选直接丢弃
    """

    def __init__(self, memory_dir: str):
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

        # 本地持久化 ChromaDB
        self._client = chromadb.PersistentClient(
            path=str(self._dir / "chroma"),
            settings=Settings(anonymized_telemetry=False),
        )
        self._col = self._client.get_or_create_collection(
            name="episodic",
            metadata={"hnsw:space": "cosine"},
        )

        # SQLite FTS5 全文索引（用于 BM25 关键词搜索）
        self._fts_db_path = str(self._dir / "fts_index.db")
        self._init_fts()

        logger.info(f"EpisodicMemory 初始化，现有 {self._col.count()} 条向量记录")

    # ── FTS5 初始化 ──────────────────────────────────────────────

    def _init_fts(self):
        """初始化 SQLite FTS5 全文索引表。"""
        conn = sqlite3.connect(self._fts_db_path)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_docs USING fts5(
                doc_id,
                doc_text,
                doc_type,
                doc_date,
                tokenize='unicode61'
            )
        """)
        # 辅助表用于去重判断
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fts_meta (
                doc_id TEXT PRIMARY KEY,
                doc_type TEXT,
                doc_date TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _upsert_fts(self, doc_id: str, text: str, doc_type: str, doc_date: str):
        """写入/更新 FTS5 索引。"""
        conn = sqlite3.connect(self._fts_db_path)
        try:
            # 先检查是否已存在
            existing = conn.execute(
                "SELECT doc_id FROM fts_meta WHERE doc_id=?", (doc_id,)
            ).fetchone()
            if existing:
                # 删除旧记录再插入（FTS5 不支持 UPDATE）
                conn.execute("DELETE FROM fts_docs WHERE doc_id=?", (doc_id,))
                conn.execute("DELETE FROM fts_meta WHERE doc_id=?", (doc_id,))
            conn.execute(
                "INSERT INTO fts_docs (doc_id, doc_text, doc_type, doc_date) VALUES (?,?,?,?)",
                (doc_id, text, doc_type, doc_date),
            )
            conn.execute(
                "INSERT INTO fts_meta (doc_id, doc_type, doc_date) VALUES (?,?,?)",
                (doc_id, doc_type, doc_date),
            )
            conn.commit()
        except Exception as e:
            logger.debug(f"FTS upsert 失败 ({doc_id}): {e}")
        finally:
            conn.close()

    def _bm25_search(self, query: str, limit: int = 20) -> Dict[str, float]:
        """
        BM25 关键词搜索，返回 {doc_id: raw_bm25_score}。
        使用 FTS5 内置的 bm25() 排序函数。

        关键发现：FTS5 unicode61 tokenizer 不会把中文按单字拆分——它把连续
        中文当作一个 token。因此：
        1. 必须指定 doc_text 列（避免搜索 doc_id 列产生噪音）
        2. 用原始查询直接搜索（不要手动拆字），让 FTS5 自身 tokenizer 处理
        3. 同时提取中文子串做额外 OR 查询，增加召回（如从"你知道石千山吗"中提取"石千山"）
        """
        # 清理查询文本：去除标点符号
        clean_q = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', query).strip()
        if not clean_q:
            return {}

        # 提取连续中文片段和英文单词
        raw_segments = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', clean_q)
        if not raw_segments:
            return {}

        # 中文长片段需要拆分：FTS5 unicode61 把连续中文当做整体 token，
        # "你知道石千山吗" 整体搜不到，但 "石千山" 可以搜到。
        # 策略：把长中文段用 2-4 字的 sliding window 拆分成 n-gram 子串。
        _STOP_CHARS = {"的", "了", "是", "在", "我", "你", "他", "她", "它",
                       "们", "和", "与", "或", "但", "也", "都", "就", "很",
                       "不", "没", "有", "这", "那", "个", "吗", "呢", "啊",
                       "吧", "嗯", "哦", "哎", "喔", "哈", "对", "好", "谁",
                       "什", "么", "怎", "样", "哪", "里", "吧", "呀"}
        segments = []
        for seg in raw_segments:
            if re.fullmatch(r'[\u4e00-\u9fff]+', seg):
                # 纯中文段：先整体加入，再生成 2~4 字 n-gram
                if len(seg) <= 4:
                    # 短词直接用（如 "石千山"、"学习"）
                    if seg not in _STOP_CHARS:
                        segments.append(seg)
                else:
                    # 长词拆成 n-gram（2,3,4 字）
                    for gram_len in (3, 2, 4):
                        for i in range(len(seg) - gram_len + 1):
                            gram = seg[i:i + gram_len]
                            # 过滤纯停用字组成的 gram
                            if not all(c in _STOP_CHARS for c in gram):
                                segments.append(gram)
            else:
                # 英文/数字直接加入
                segments.append(seg)

        # 去重保序
        seen = set()
        meaningful = []
        for s in segments:
            if s not in seen:
                seen.add(s)
                meaningful.append(s)

        if not meaningful:
            return {}

        # 构造 FTS5 MATCH 表达式：指定 doc_text 列，用 OR 连接
        # 例如: doc_text : "石千山" OR doc_text : "最近"
        match_parts = [f'doc_text : "{seg}"' for seg in meaningful]
        match_expr = " OR ".join(match_parts)

        conn = sqlite3.connect(self._fts_db_path)
        results = {}
        try:
            rows = conn.execute(
                """
                SELECT doc_id, bm25(fts_docs) AS score
                FROM fts_docs
                WHERE fts_docs MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (match_expr, limit),
            ).fetchall()
            for doc_id, score in rows:
                results[doc_id] = abs(score)  # bm25() 返回负分，取绝对值
        except Exception as e:
            # MATCH 可能因为 query 格式问题失败，静默处理
            logger.warning(f"BM25 搜索失败 (expr={match_expr!r}): {e}")
        finally:
            conn.close()

        if results:
            logger.info(f"[BM25] 关键词命中 {len(results)} 条 (query_segments={meaningful})")

        return results

    # ── 写入 ──────────────────────────────────────────────────────

    def add(
        self,
        text: str,
        doc_type: str,          # "activity" | "diary" | "report"
        doc_date: str,          # "2026-04-19"
        metadata: Optional[Dict] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        """添加一条情节记忆，同时写入向量库和 FTS5 索引。"""
        from llm_client import embed

        vec = embed(text)
        _id = doc_id or f"{doc_type}_{doc_date}_{datetime.now().strftime('%H%M%S%f')}"
        meta = {
            "type": doc_type,
            "date": doc_date,
            **(metadata or {}),
        }

        # 写入 ChromaDB
        self._col.upsert(
            ids=[_id],
            embeddings=[vec],
            documents=[text],
            metadatas=[meta],
        )
        # 写入 FTS5 索引
        self._upsert_fts(_id, text, doc_type, doc_date)

        logger.debug(f"EpisodicMemory 写入：{_id} ({doc_type})")
        return _id

    def add_activity_batch(self, activities: List[Dict], batch_date: str):
        """
        每天采集结束后，把活动段批量写入（合并为自然语言段落）
        不逐条写入，按类别合并后写，减少 embedding 调用次数
        """
        if not activities:
            return

        # 按类别分组
        groups: Dict[str, List[Dict]] = {}
        for a in activities:
            cat = a.get("app_category", "other")
            groups.setdefault(cat, []).append(a)

        for cat, items in groups.items():
            total_min = sum(i.get("duration_sec", 0) for i in items) // 60
            apps = list({i["process_name"].replace(".exe", "") for i in items})
            titles = [i["window_title"][:30] for i in items[:3]]
            text = (
                f"{batch_date} 在 {cat} 类活动上花了 {total_min} 分钟，"
                f"使用了 {', '.join(apps)}，"
                f"主要窗口：{'; '.join(titles)}"
            )
            self.add(
                text=text,
                doc_type="activity",
                doc_date=batch_date,
                metadata={"category": cat, "duration_min": total_min},
                doc_id=f"activity_{batch_date}_{cat}",
            )
        logger.info(f"活动段批量写入完成：{batch_date}，共 {len(groups)} 个类别")

    def add_diary(self, content: str, diary_date: str, source_file: str):
        """导入一篇 Obsidian 日记"""
        # 长日记分块（每 500 字一块），过滤掉太短的块（图片/附件替换后可能产生空块）
        chunks = _chunk_text(content, chunk_size=500, overlap=50)
        valid_chunks = [c for c in chunks if len(c.strip()) >= 20]
        if not valid_chunks:
            logger.warning(f"日记内容过短，跳过：{diary_date} ({source_file})")
            return
        saved = 0
        for i, chunk in enumerate(valid_chunks):
            try:
                self.add(
                    text=chunk,
                    doc_type="diary",
                    doc_date=diary_date,
                    metadata={"source": source_file, "chunk": i},
                    doc_id=f"diary_{diary_date}_{i}",
                )
                saved += 1
            except Exception as e:
                logger.warning(f"块 {i} embedding 失败，跳过：{diary_date} - {e}")
        if saved == 0:
            raise RuntimeError(f"所有块均 embedding 失败：{diary_date}")
        logger.info(f"日记导入：{diary_date}，{saved}/{len(valid_chunks)} 个块")

    def add_report(self, summary: str, report_date: str):
        """写入日报摘要"""
        self.add(
            text=summary,
            doc_type="report",
            doc_date=report_date,
            doc_id=f"report_{report_date}",
        )

    # ── 检索 ──────────────────────────────────────────────────────

    def search(self, query: str, n: int = 3, doc_type: Optional[str] = None) -> List[Dict]:
        """
        Phase 2 混合检索（修复版）：语义 + BM25 双通道独立召回，合并去重后统一精排。

        关键改进：BM25 关键词命中可以独立召回候选（不再依赖语义阈值前置过滤），
        解决了"石千山"等精确关键词被语义门控误杀的问题。

        架构:
          1. 语义通道 → 召回 semantic > SEMANTIC_THRESHOLD 的候选
          2. BM25 通道 → 召回关键词命中的候选（独立于语义分数）
          3. 合并去重 → 两个通道的并集
          4. 统一打分 → final = w_sem * semantic + w_bm25 * bm25 + w_rec * recency
          5. 最终门控 → final >= FINAL_SCORE_THRESHOLD
        """
        from llm_client import embed

        vec = embed(query)
        where_filter: dict | None = {"type": {"$eq": doc_type}} if doc_type else None

        # ── Step 1: 语义搜索通道 ──────────────────────────────────
        internal_limit = max(n * 4, 20)
        semantic_pool = {}  # doc_id → {text, date, type, semantic_score, metadata}

        try:
            results = self._col.query(
                query_embeddings=[vec],
                n_results=min(internal_limit, self._col.count()) if self._col.count() > 0 else 1,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"ChromaDB 查询失败：{e}")
            return []

        docs = results.get("documents") or [[]]
        metas = results.get("metadatas") or [[]]
        dists = results.get("distances") or [[]]
        ids = results.get("ids") or [[]]

        # 所有语义结果都记录（后续需要给 BM25 候选补语义分数），
        # 但只有过阈值的才进入 semantic_pool
        all_semantic = {}  # doc_id → semantic_score（全量，含低分）
        for doc_id, doc, meta, dist in zip(ids[0], docs[0], metas[0], dists[0]):
            semantic_score = round(1 - dist, 4)
            all_semantic[doc_id] = semantic_score
            if semantic_score >= SEMANTIC_THRESHOLD:
                semantic_pool[doc_id] = {
                    "text": doc,
                    "date": meta.get("date", ""),
                    "type": meta.get("type", ""),
                    "semantic_score": semantic_score,
                    "metadata": meta,
                }

        # ── Step 2: BM25 关键词搜索通道（独立召回）────────────────
        bm25_raw = self._bm25_search(query, limit=internal_limit)
        midpoint, steepness = _get_bm25_sigmoid_params(query)

        bm25_scores = {}
        for doc_id, raw in bm25_raw.items():
            bm25_scores[doc_id] = _normalize_bm25(raw, midpoint, steepness)

        # BM25 命中但不在 semantic_pool 中的候选 → 从 ChromaDB 补全元数据
        bm25_only_ids = [did for did in bm25_scores if did not in semantic_pool]
        if bm25_only_ids:
            try:
                bm25_docs = self._col.get(
                    ids=bm25_only_ids,
                    include=["documents", "metadatas"],
                )
                for doc_id, doc, meta in zip(
                    bm25_docs.get("ids") or [],
                    bm25_docs.get("documents") or [],
                    bm25_docs.get("metadatas") or [],
                ):
                    semantic_pool[doc_id] = {
                        "text": doc,
                        "date": meta.get("date", ""),
                        "type": meta.get("type", ""),
                        "semantic_score": all_semantic.get(doc_id, 0.0),
                        "metadata": meta,
                    }
            except Exception as e:
                logger.debug(f"BM25 候选元数据补全失败: {e}")

        if not semantic_pool:
            logger.info(f"[Memory] 无记忆通过双通道召回 "
                        f"(sem_threshold={SEMANTIC_THRESHOLD}, bm25_hits={len(bm25_scores)})")
            return []

        # ── Step 3: 统一精排 ─────────────────────────────────────
        scored_results = []
        w_sem = SIGNAL_WEIGHTS["semantic"]
        w_bm25 = SIGNAL_WEIGHTS["bm25"]
        w_rec = SIGNAL_WEIGHTS["recency"]

        for doc_id, cand in semantic_pool.items():
            sem = cand["semantic_score"]
            bm25 = bm25_scores.get(doc_id, 0.0)
            rec = _recency_score(cand["date"], doc_type=cand["type"])

            final = w_sem * sem + w_bm25 * bm25 + w_rec * rec

            scored_results.append({
                "text": cand["text"],
                "date": cand["date"],
                "type": cand["type"],
                "score": round(final, 4),
                "semantic_score": sem,
                "bm25_score": round(bm25, 4),
                "recency_score": round(rec, 4),
                "metadata": cand["metadata"],
            })

        # 按综合分数降序排列，再过滤最终分数
        scored_results.sort(key=lambda x: x["score"], reverse=True)
        scored_results = [r for r in scored_results if r["score"] >= FINAL_SCORE_THRESHOLD]

        top_n = scored_results[:n]
        if top_n:
            logger.info(
                f"[Memory] 混合检索 top-{len(top_n)}: "
                f"best={top_n[0]['score']:.3f} "
                f"(sem={top_n[0]['semantic_score']:.3f}, "
                f"bm25={top_n[0]['bm25_score']:.3f}, "
                f"rec={top_n[0]['recency_score']:.3f})"
            )
        else:
            logger.info(f"[Memory] 双通道共 {len(semantic_pool)} 个候选，"
                        f"但无一通过最终门控 (threshold={FINAL_SCORE_THRESHOLD})")

        return top_n

    def search_recent(self, days: int = 7, n: int = 10) -> List[Dict]:
        """获取最近 N 天的记忆"""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        try:
            results = self._col.get(
                where={"date": {"$gte": cutoff}},  # type: ignore[arg-type]
                include=["documents", "metadatas"],
                limit=n,
            )
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []
            return [
                {"text": doc, "date": meta.get("date", ""), "type": meta.get("type", "")}
                for doc, meta in zip(docs, metas)
            ]
        except Exception as e:
            logger.error(f"recent 查询失败：{e}")
            return []

    def count(self) -> int:
        return self._col.count()

    def delete_by_date(self, diary_date: str):
        """删除指定日期的所有日记块（用于 retry-vision 时先清旧数据）"""
        try:
            results = self._col.get(
                where={"$and": [{"type": {"$eq": "diary"}}, {"date": {"$eq": diary_date}}]},
                include=[],
            )
            ids = results.get("ids", [])
            if ids:
                self._col.delete(ids=ids)
                # 同步删除 FTS 索引
                conn = sqlite3.connect(self._fts_db_path)
                try:
                    for _id in ids:
                        conn.execute("DELETE FROM fts_docs WHERE doc_id=?", (_id,))
                        conn.execute("DELETE FROM fts_meta WHERE doc_id=?", (_id,))
                    conn.commit()
                finally:
                    conn.close()
                logger.debug(f"已删除 {diary_date} 的 {len(ids)} 个旧块")
        except Exception as e:
            logger.warning(f"删除 {diary_date} 旧条目失败：{e}")

    def to_context_text(self, query: str, n: int = 3) -> str:
        """
        生成给 LLM 的相关记忆上下文。
        Phase 1 改进：清晰的分层标记格式，便于 LLM 区分记忆和用户消息。
        """
        results = self.search(query, n=n)
        if not results:
            return ""
        lines = []
        for r in results:
            # 截断过长文本，保留关键信息
            text = r['text'][:200].replace('\n', ' ')
            lines.append(f"- [{r['date']}][{r['type']}](相关度:{r['score']:.2f}) {text}")
        return "\n".join(lines)

    def rebuild_fts_index(self):
        """从 ChromaDB 重建 FTS5 索引（一次性迁移用）。"""
        logger.info("[FTS] 开始重建全文索引...")
        total = self._col.count()
        if total == 0:
            logger.info("[FTS] 向量库为空，跳过")
            return

        batch_size = 100
        rebuilt = 0
        offset = 0
        while offset < total:
            results = self._col.get(
                include=["documents", "metadatas"],
                limit=batch_size,
                offset=offset,
            )
            ids = results.get("ids") or []
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []

            for doc_id, doc, meta in zip(ids, docs, metas):
                self._upsert_fts(
                    doc_id, doc,
                    str(meta.get("type", "unknown")),
                    str(meta.get("date", "")),
                )
                rebuilt += 1

            offset += batch_size

        logger.info(f"[FTS] 全文索引重建完成，共 {rebuilt} 条")


# ── 工具函数 ──────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """把长文本按字数分块"""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


# 全局单例
_episodic: Optional[EpisodicMemory] = None


def get_episodic_memory(memory_dir: Optional[str] = None) -> EpisodicMemory:
    global _episodic
    if _episodic is None:
        if memory_dir is None:
            from config import get_config
            memory_dir = get_config().memory_dir
        _episodic = EpisodicMemory(memory_dir)
    return _episodic