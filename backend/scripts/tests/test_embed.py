"""临时调试脚本：直接用 requests 测 MiniMax embedding API"""
import os, sys
import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(__import__("pathlib").Path(__file__).parent.parent / ".env")

api_key = os.getenv("MINIMAX_API_KEY", "")
print(f"API Key 前8位: {api_key[:8]}...")

test_text = "今天是个好天气，我去公园散步了。"
print(f"\n[1] 测试文本: {test_text}")
print("[2] 发送 requests.post 请求...")

resp = requests.post(
    "https://api.minimax.chat/v1/embeddings",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    json={"model": "embo-01", "texts": [test_text], "type": "db"},
    timeout=30,
)

print(f"[3] HTTP 状态码: {resp.status_code}")
data = resp.json()
print(f"[4] 响应 keys: {list(data.keys())}")
print(f"[5] 完整响应（截断）: {str(data)[:300]}")

vectors = data.get("vectors")
if vectors:
    print(f"\n✅ vectors 字段存在，长度: {len(vectors)}")
    print(f"   vectors[0] 类型: {type(vectors[0])}")
    print(f"   vectors[0] 长度: {len(vectors[0]) if isinstance(vectors[0], list) else 'N/A'}")
    print(f"   vectors[0] 前3个值: {vectors[0][:3] if isinstance(vectors[0], list) else vectors[0]}")
else:
    print(f"\n❌ 没有 vectors 字段，完整响应:")
    import json
    print(json.dumps(data, ensure_ascii=False, indent=2))
