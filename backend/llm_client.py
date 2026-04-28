"""
Navi LLM 客户端 v2
配置优先级：config.yaml [llm] > .env > 硬编码默认值

对外接口：
  - embed(text)                              → List[float]
  - chat(messages, system, ...)              → str
  - vision_chat(image_b64, prompt, ...)      → str | None
  - vision_batch(images_b64, prompt, ...)    → str | None

支持厂商（均走 OpenAI 兼容协议）：
  doubao / deepseek / openai / anthropic / gemini /
  minimax / moonshot / qwen / siliconflow / openrouter / custom
"""
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger("navi.llm")

# ---------------------------------------------------------------------------
# 配置读取（与 config.py 解耦，避免循环依赖）
# ---------------------------------------------------------------------------

def _load_llm_cfg() -> Dict[str, str]:
    """
    从 ~/.navi/config.yaml 读取 [llm] 节，合并 .env 作为兜底。
    每次调用都重新读文件，保证 UI 保存后立即生效（无缓存）。
    """
    cfg: Dict[str, str] = {}
    try:
        import yaml
        navi_home = Path.home() / ".navi"
        config_path = navi_home / "config.yaml"
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg = {k: str(v) for k, v in (raw.get("llm") or {}).items() if v}
    except Exception as e:
        logger.debug(f"读取 llm config 失败，降级到 .env: {e}")

    # .env 兜底（向后兼容旧版本）
    if not cfg.get("vision_api_key"):
        cfg["vision_api_key"]  = os.getenv("MINIMAX_API_KEY", "")
    if not cfg.get("vision_base_url"):
        cfg["vision_base_url"] = "https://api.minimax.chat/v1"
    if not cfg.get("vision_model"):
        cfg["vision_model"]    = os.getenv("MINIMAX_CHAT_MODEL", "MiniMax-M2.5")

    if not cfg.get("text_api_key"):
        cfg["text_api_key"]    = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("MINIMAX_API_KEY", "")
    if not cfg.get("text_base_url"):
        if os.getenv("DEEPSEEK_API_KEY"):
            cfg["text_base_url"] = "https://api.deepseek.com"
        else:
            cfg["text_base_url"] = "https://api.minimax.chat/v1"
    if not cfg.get("text_model"):
        cfg["text_model"]      = "deepseek-chat" if os.getenv("DEEPSEEK_API_KEY") else os.getenv("MINIMAX_CHAT_MODEL", "MiniMax-M2.5")

    if not cfg.get("emb_api_key"):
        cfg["emb_api_key"]     = os.getenv("MINIMAX_API_KEY", "")
    if not cfg.get("emb_base_url"):
        cfg["emb_base_url"]    = "https://api.minimax.chat/v1"
    if not cfg.get("emb_model"):
        cfg["emb_model"]       = "embo-01"

    return cfg


# ---------------------------------------------------------------------------
# 通用 OpenAI 兼容调用
# ---------------------------------------------------------------------------

from llm_constants import (
    LLM_CHAT_MAX_TOKENS, LLM_CHAT_TEMPERATURE,
    LLM_VISION_MAX_TOKENS, LLM_VISION_BATCH_MAX_TOKENS,
)

def _openai_chat(
    api_key: str,
    base_url: str,
    model: str,
    messages: List[dict],
    temperature: float = LLM_CHAT_TEMPERATURE,
    max_tokens: int = LLM_CHAT_MAX_TOKENS,
) -> str:
    """所有厂商统一走 OpenAI 兼容协议"""
    from openai import OpenAI
    client = OpenAI(api_key=api_key or "none", base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )
    raw = (resp.choices[0].message.content or "").strip()
    # 剥离 <think>...</think> 推理链（MiniMax-M2.5 / DeepSeek-R1 思维模式）
    result = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
    # 注意：result 为空说明模型只输出了思考链，token 被耗尽；直接返回空串，不回退到 raw
    return result


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def embed(text: str) -> List[float]:
    """
    文本 → 向量
    MiniMax embo-01 走独立原生 API（格式与 OpenAI 不兼容）；其余走标准接口。
    """
    cfg = _load_llm_cfg()
    api_key  = cfg.get("emb_api_key", "")
    base_url = cfg.get("emb_base_url", "https://api.minimax.chat/v1")
    model    = cfg.get("emb_model", "embo-01")

    if not api_key:
        logger.warning("[embed] 未配置 emb_api_key，返回零向量（仅开发调试）")
        return [0.0] * 1536

    if model == "embo-01" and "minimax" in base_url:
        return _minimax_embed(text, api_key)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.embeddings.create(model=model, input=text)
        return resp.data[0].embedding
    except Exception as e:
        logger.error(f"[embed] 调用失败: {e}")
        return [0.0] * 1536


def _minimax_embed(text: str, api_key: str) -> List[float]:
    """MiniMax embo-01 原生格式（vectors 字段）"""
    import requests
    resp = requests.post(
        "https://api.minimax.chat/v1/embeddings",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": "embo-01", "texts": [text], "type": "db"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    vectors = data.get("vectors")
    if vectors and vectors[0]:
        return vectors[0]
    items = data.get("data", [])
    if items and items[0].get("embedding"):
        return items[0]["embedding"]
    raise ValueError(f"No embedding data: {data}")


def chat(
    messages: List[dict],
    system: str = "你是 Navi，用户的个人 AI 助手。",
    temperature: float = LLM_CHAT_TEMPERATURE,
    max_tokens: int = LLM_CHAT_MAX_TOKENS,
    model: str = "",
) -> str:
    """
    文本对话，统一走 OpenAI 兼容协议。
    model 参数可覆盖 config 里的 text_model。
    """
    cfg = _load_llm_cfg()
    api_key  = cfg.get("text_api_key", "")
    base_url = cfg.get("text_base_url", "")
    _model   = model or cfg.get("text_model", "")

    if not api_key:
        raise RuntimeError(
            "未配置 text_api_key，请在「AI 模型」设置页填写，或在 .env 中设置 DEEPSEEK_API_KEY / MINIMAX_API_KEY"
        )

    logger.info(f"[chat] base={base_url!r} model={_model!r}")
    full_messages = [{"role": "system", "content": system}] + messages
    return _openai_chat(api_key, base_url, _model, full_messages, temperature, max_tokens)


def vision_chat(
    image_b64: str,
    prompt: str = "请用简洁的中文描述这张图片的内容，控制在100字以内。",
    max_tokens: int = LLM_VISION_MAX_TOKENS,
) -> Optional[str]:
    """单图理解，走 OpenAI 兼容 vision 接口。"""
    cfg = _load_llm_cfg()
    api_key  = cfg.get("vision_api_key", "")
    base_url = cfg.get("vision_base_url", "")
    model    = cfg.get("vision_model", "")

    if not api_key:
        logger.warning("[vision_chat] 未配置 vision_api_key，跳过图片分析")
        return None

    logger.info(f"[vision_chat] base={base_url!r} model={model!r}")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = (resp.choices[0].message.content or "").strip()
        result = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
        return result or None
    except Exception as e:
        logger.warning(f"[vision_chat] 调用失败: {e}")
        return None


def vision_batch(
    images_b64: List[str],
    prompt: str,
    max_tokens: int = LLM_VISION_BATCH_MAX_TOKENS,
) -> Optional[str]:
    """多图批量 Vision，走 OpenAI 兼容 vision 接口。"""
    cfg = _load_llm_cfg()
    api_key  = cfg.get("vision_api_key", "")
    base_url = cfg.get("vision_base_url", "")
    model    = cfg.get("vision_model", "")

    if not api_key:
        logger.warning("[vision_batch] 未配置 vision_api_key，跳过")
        return None

    logger.info(f"[vision_batch] {len(images_b64)} 张图 base={base_url!r} model={model!r}")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        content: List[Any] = []
        for b64 in images_b64:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        content.append({"type": "text", "text": prompt})

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
        )
        raw = (resp.choices[0].message.content or "").strip()
        result = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
        return result or raw or None
    except Exception as e:
        logger.error(f"[vision_batch] 调用失败: {e}")
        return None
