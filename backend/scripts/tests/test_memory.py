"""验证 ChromaDB 导入效果：用几个问题做语义搜索"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_config
from memory.episodic_memory import get_episodic_memory

config = get_config()
episodic = get_episodic_memory(config.memory_dir)

print(f"📦 ChromaDB 总记录数：{episodic.count()}\n")

queries = [
    "学习编程",
    "实习经历",
    "心情低落",
    "旅行",
]

for q in queries:
    print(f"🔍 搜索：「{q}」")
    results = episodic.search(q, n=2, doc_type="diary")
    if results:
        for r in results:
            print(f"   [{r['date']}] 相似度:{r['score']}  {r['text'][:60]}...")
    else:
        print("   无结果")
    print()
