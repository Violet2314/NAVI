"""
测试当前 config.yaml / .env 配置的 LLM 是否可用。

用法：
  cd D:\\learn\\Navi\\backend
  uv run python scripts/tests/test_llm_config.py

会依次测试：
  1. 读取配置（打印脱敏结果）
  2. Text Chat（发一条简短问候）
  3. Vision（用一张纯色图测试）
  4. Embedding（对一段文字做向量化）
"""
import sys
import os
import time
import base64

# 把 backend 目录加到 sys.path（test 在 backend/scripts/tests/，往上一层是 backend/scripts，往上两层才是 backend/）
_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(_backend_dir, ".env"))


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def mask(v: str) -> str:
    return v[:4] + "****" if v and len(v) > 4 else ("（未配置）" if not v else "****")

def ok(msg): print(f"  ✅ {msg}")
def fail(msg): print(f"  ❌ {msg}")
def section(title): print(f"\n{'='*50}\n  {title}\n{'='*50}")


# ─────────────────────────────────────────────
# 1. 显示当前生效配置
# ─────────────────────────────────────────────

section("1. 当前生效配置")
from llm_client import _load_llm_cfg
cfg = _load_llm_cfg()

roles = [
    ("Vision（截图分析）", "vision"),
    ("Text（日报撰写）",   "text"),
    ("Embedding（记忆）",  "emb"),
]
for label, field in roles:
    print(f"\n  [{label}]")
    print(f"    厂商:    {cfg.get(f'{field}_provider', '（未设置）')}")
    print(f"    模型:    {cfg.get(f'{field}_model', '（未设置）')}")
    print(f"    API Key: {mask(cfg.get(f'{field}_api_key', ''))}")
    print(f"    Base URL:{cfg.get(f'{field}_base_url', '（未设置）')}")


# ─────────────────────────────────────────────
# 2. Text Chat 测试
# ─────────────────────────────────────────────

section("2. Text Chat 测试")
from llm_client import chat

try:
    t0 = time.time()
    reply = chat(
        messages=[{"role": "user", "content": "你好，只需回复「Navi OK」四个字。"}],
        system="你是 Navi。",
        max_tokens=200,  # 思维链模型需要足够 token 完成 think 块后才能输出正文
    )
    elapsed = time.time() - t0
    ok(f"回复 ({elapsed:.1f}s): {reply!r}")
except Exception as e:
    fail(f"Text Chat 失败: {e}")


# ─────────────────────────────────────────────
# 3. Vision 测试（生成一张 1x1 红色 JPEG）
# ─────────────────────────────────────────────

section("3. Vision 测试")
from llm_client import vision_chat

try:
    # 用 PIL 生成一张最小测试图，如果没装 PIL 就用预置的 base64
    try:
        from PIL import Image
        import io
        img = Image.new("RGB", (64, 64), color=(220, 50, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        img_b64 = base64.b64encode(buf.getvalue()).decode()
        print("  （使用 PIL 生成 64×64 红色测试图）")
    except ImportError:
        # 最小合法 JPEG (1x1 红色像素)
        img_b64 = (
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
            "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
            "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
            "MjL/wAARCAABAAEDASIAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAAAAAABgUE/8QAIBAAAg"
            "ICAwEBAAAAAAAAAAAAAQIDBAUREiExQf/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEA"
            "AAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCwcbjMfislJQtK2hNVJOMoxkorb8ySS"
            "SSST7JJJJJP/9k="
        )
        print("  （使用内置 1×1 测试图，PIL 未安装）")

    t0 = time.time()
    desc = vision_chat(img_b64, prompt="这张图片是什么颜色？只需一个词回答。", max_tokens=200)
    elapsed = time.time() - t0
    if desc:
        ok(f"Vision 回复 ({elapsed:.1f}s): {desc!r}")
    else:
        fail("Vision 返回 None（Key 未配置或模型不支持图片）")
except Exception as e:
    fail(f"Vision 测试失败: {e}")


# ─────────────────────────────────────────────
# 4. Embedding 测试
# ─────────────────────────────────────────────

section("4. Embedding 测试")
from llm_client import embed

try:
    t0 = time.time()
    vec = embed("今天学习了 Python 异步编程")
    elapsed = time.time() - t0

    if not vec or all(v == 0.0 for v in vec):
        fail(f"Embedding 返回零向量（Key 未配置）")
    else:
        dim = len(vec)
        norm = sum(x*x for x in vec) ** 0.5
        ok(f"向量维度={dim}, 模长={norm:.4f}, 耗时={elapsed:.1f}s")
        ok(f"前5维: {[round(x, 5) for x in vec[:5]]}")
except Exception as e:
    fail(f"Embedding 测试失败: {e}")


# ─────────────────────────────────────────────
# 5. 总结
# ─────────────────────────────────────────────

section("测试完成")
print("  如有 ❌，请检查：")
print("  1. 在 UI「AI 模型」Tab 保存了正确的 Key + 模型名")
print("  2. 或在 backend/.env 中填写 MINIMAX_API_KEY / DEEPSEEK_API_KEY")
print(f"  配置文件路径: {os.path.expanduser('~/.navi/config.yaml')}")
print()
