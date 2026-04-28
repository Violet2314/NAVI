"""
Cold Start Bootstrap：从已有的 ChromaDB 日记中批量提取 L3 用户事实。

用途：第一次启用 Phase 3 后运行一次，把 196 条日记浓缩为用户画像。
运行：cd backend && uv run python scripts/bootstrap_facts.py

注意：
- 会消耗 LLM token（每 5 条日记合并为一次 LLM 调用）
- 幂等：重复运行不会产生重复 facts（Hash 去重）
- 预计耗时：196 条日记 ≈ 40 次 LLM 调用 ≈ 3-5 分钟
"""
import json
import logging
import sys
import time
from pathlib import Path

# 确保 backend 目录在 sys.path 中
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bootstrap")

# ── 提取 Prompt（针对日记优化，不需要事件摘要）────────────────────
_DIARY_EXTRACTION_PROMPT = """\
你是 Navi 的记忆归档系统。请从以下用户日记中提取关于用户的**客观事实**。

## 提取规则
1. 只提取用户明确表达的信息，不推测
2. 每条 fact 必须是原子级的（一条只描述一个事实）
3. 分类：
   - preference（偏好，如喜欢什么、讨厌什么）
   - status（当前状态，如在哪里工作/学习）
   - relationship（人际关系，如朋友、家人）
   - skill（技能，如会什么编程语言）
   - habit（习惯，如每天做什么）
   - biographical（个人信息，如名字、学校、年龄）
   - goal（目标，如想做什么、计划什么）
   - project（项目，如正在做什么项目/产品）
4. 只提取有**长期价值**的信息：
   - ✅ "我在广东财经大学读书"
   - ✅ "我在学 Go 语言"
   - ✅ "我拿到了网易的 offer"
   - ❌ "我今天很累"（太短暂）
   - ❌ "今天天气不好"（无关用户）
5. 如果同一件事在多篇日记中出现，只提取一次
6. 提取尽可能完整，不要遗漏重要信息

## 日记内容
{diaries}

## 输出（严格 JSON 数组，不要输出其他内容）
[
  {{"content": "事实内容", "category": "分类", "confidence": 0.9}},
  ...
]

如果日记中没有值得提取的信息，返回空数组 []。"""


def extract_facts_from_diaries(diary_texts: list[dict], llm_call) -> list[dict]:
    """从一批日记中提取 facts。"""
    # 格式化日记
    parts = []
    for d in diary_texts:
        date = d.get("date", "未知日期")
        text = d.get("text", "").strip()
        if text:
            parts.append(f"--- [{date}] ---\n{text}")

    if not parts:
        return []

    prompt = _DIARY_EXTRACTION_PROMPT.format(diaries="\n\n".join(parts))

    try:
        response = llm_call(prompt)
        # 尝试解析 JSON
        text = response.strip()
        # 处理 markdown 包裹
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()

        facts = json.loads(text)
        if isinstance(facts, list):
            return facts
        elif isinstance(facts, dict) and "facts" in facts:
            return facts["facts"]
        return []
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"  JSON 解析失败: {e}")
        logger.debug(f"  原始响应: {response[:200]}")
        return []


def main():
    from config import get_config
    from memory.episodic_memory import get_episodic_memory
    from memory.fact_memory import get_fact_memory

    cfg = get_config()

    print("=" * 60)
    print("  Cold Start Bootstrap: 从日记提取用户画像")
    print("=" * 60)

    # 1. 获取所有日记
    em = get_episodic_memory()
    total = em._col.count()
    print(f"\nChromaDB 总记录数: {total}")

    # 只提取日记类型的记录
    all_records = em._col.get(
        where={"type": {"$eq": "diary"}},
        include=["documents", "metadatas"],
    )
    diary_ids = all_records.get("ids", [])
    diary_docs = all_records.get("documents", [])
    diary_metas = all_records.get("metadatas", [])

    print(f"日记记录数: {len(diary_ids)}")

    if not diary_ids:
        print("没有日记记录，跳过。")
        return

    # 2. 准备 LLM 调用
    from llm_client import chat as llm_chat
    def llm_call(prompt: str) -> str:
        """同步调用 LLM。"""
        messages = [{"role": "user", "content": prompt}]
        return llm_chat(
            messages,
            system="你是一个精确的信息提取系统。只输出 JSON，不输出任何其他内容。",
            temperature=0.3,
        )

    # 3. 分批提取（每批 5 条日记）
    BATCH_SIZE = 5
    fm = get_fact_memory()

    existing_count = fm.count()
    print(f"已有 Facts 数量: {existing_count}")
    print(f"\n开始提取（每批 {BATCH_SIZE} 条日记，预计 {len(diary_ids) // BATCH_SIZE + 1} 次 LLM 调用）...")
    print("-" * 60)

    total_extracted = 0
    total_added = 0
    batch_num = 0

    for i in range(0, len(diary_ids), BATCH_SIZE):
        batch_num += 1
        batch_docs = []
        for j in range(i, min(i + BATCH_SIZE, len(diary_ids))):
            doc = diary_docs[j] if diary_docs else ""
            meta = diary_metas[j] if diary_metas else {}
            batch_docs.append({
                "text": doc or "",
                "date": meta.get("date", "unknown") if isinstance(meta, dict) else "unknown",
            })

        date_range = f"{batch_docs[0]['date']} ~ {batch_docs[-1]['date']}"
        print(f"\n[Batch {batch_num}] 日期范围: {date_range} ({len(batch_docs)} 条)")

        facts = extract_facts_from_diaries(batch_docs, llm_call)
        total_extracted += len(facts)
        print(f"  提取到 {len(facts)} 条 facts")

        if facts:
            added = fm.add_facts(facts)
            total_added += added
            print(f"  新增 {added} 条（去重后）")
            for f in facts:
                print(f"    [{f.get('category', '?')}] {f.get('content', '?')}")

        # 控制请求频率，避免 rate limit
        time.sleep(0.5)

    print("\n" + "=" * 60)
    print(f"  提取完成！")
    print(f"  处理日记: {len(diary_ids)} 条")
    print(f"  提取 Facts: {total_extracted} 条")
    print(f"  实际新增: {total_added} 条（去重后）")
    print(f"  当前 Facts 总数: {fm.count()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
