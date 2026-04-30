"""诊断：检查 ChromaDB 里图片描述的质量分布"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_config
from memory.episodic_memory import get_episodic_memory

config = get_config()
episodic = get_episodic_memory(config.memory_dir)

results = episodic._col.get(
    where={"type": "diary"},  # type: ignore[arg-type]
    include=["metadatas", "documents"],
)

docs = results.get("documents") or []
metas = results.get("metadatas") or []

# 统计
total_blocks = len(docs)
has_desc = 0       # [图片描述：xxx] 正常
has_bad_desc = 0   # 描述了但内容是废话
has_placeholder = 0  # [图片：xxx] 占位符
no_image = 0       # 无图片

BAD_MARKERS = ["没有看到您实际上传", "没有看到您上传", "未看到图片", "没有收到图片", "图片似乎没有"]

for doc in docs:
    if not doc:
        continue
    if "[图片描述：" in doc:
        bad = any(m in doc for m in BAD_MARKERS)
        if bad:
            has_bad_desc += 1
        else:
            has_desc += 1
    elif "[图片：" in doc:
        has_placeholder += 1
    else:
        no_image += 1

print(f"📊 ChromaDB 图片描述质量报告")
print(f"   总块数：{total_blocks}")
print(f"   ✅ 正常图片描述：{has_desc} 块")
print(f"   ❌ 废话描述（Anthropic格式错误）：{has_bad_desc} 块")
print(f"   ⚠️  占位符（图片缺失/跳过）：{has_placeholder} 块")
print(f"   📝 无图片（纯文字）：{no_image} 块")

if has_bad_desc > 0:
    print(f"\n需要重试的废话描述示例（前3条）：")
    count = 0
    for doc, meta in zip(docs, metas):
        if doc and any(m in doc for m in BAD_MARKERS):
            print(f"  [{meta.get('date')}] {doc[:100]}...")
            count += 1
            if count >= 3:
                break
