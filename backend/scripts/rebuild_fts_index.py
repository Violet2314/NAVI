"""
一次性迁移脚本：从 ChromaDB 向量库重建 SQLite FTS5 全文索引。
运行方式：cd backend && python scripts/rebuild_fts_index.py
"""
import sys
from pathlib import Path

# 确保 backend 目录在 sys.path 中
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from memory.episodic_memory import get_episodic_memory


def main():
    print("=== FTS5 全文索引重建工具 ===")
    em = get_episodic_memory()
    print(f"ChromaDB 中共有 {em.count()} 条向量记录")
    em.rebuild_fts_index()
    print("完成！")


if __name__ == "__main__":
    main()
