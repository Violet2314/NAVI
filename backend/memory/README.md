# Navi 记忆系统架构

## SSOT（唯一权威）

| 数据类型 | SSOT 位置 | 写入路径 | 读取路径 |
|---|---|---|---|
| 用户事实（偏好、习惯、技能） | SQLite `user_facts` 表 | `FactMemory.add_facts`（仅由 ingestion worker 调用） | `FactMemory.to_context_text` |
| 会话叙事记忆（项目笔记、决策） | `workspace/memory/MEMORY.md` | `MemoryStore.write_long_term`（LLM 调 save_memory tool） | `MemoryStore.get_memory_context` |
| 情节记忆（会话摘要） | ChromaDB `episodic_memory` collection | `EpisodicMemory.add` | `EpisodicMemory.search` |
| 实时活动 | 内存 ring buffer | `WorkingMemory.append` | `WorkingMemory.to_context_text` |
| 用户人设视图 | `templates/USER.md` | `UserModelManager.render_from_facts`（**只读投影**） | system prompt |
| Soul 人设 | `templates/SOUL.md` | 人工编辑 | system prompt |

## 重要约束

1. **USER.md 是 FactMemory 的 view，不是 SSOT**。手动编辑会被下次 session 结束时覆盖。
2. **MEMORY.md 不要写用户事实**。`MemoryStore` 内部有 `_strip_user_facts` 过滤，但请在 prompt 引导 LLM 自觉。
3. **save_memory tool 只用于叙事**：项目进度、决策、跨会话上下文。"用户喜欢咖啡"这种事实由 ingestion pipeline 自动从对话提取。
4. **删除事实**：用 `FactMemory.supersede(old_id, new_content)`。永远不要 DELETE。

## 写入触发图

```
用户对话 ─┬─→ messages → loop 处理 → response
          │
          └─→ on_pre_compress hook
                ├─→ ingestion_worker.enqueue(extract_facts)  → FactMemory
                └─→ MemoryConsolidator.summarize()           → EpisodicMemory
                                                              → MEMORY.md（叙事段）

session 结束 ─→ UserModelManager.render_from_facts → USER.md
```
