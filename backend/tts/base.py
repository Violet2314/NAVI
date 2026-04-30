"""TTS Provider 基类 + 注册表。

所有 Provider 实现同一个接口：
  async def synthesize(text, voice_id, **kwargs) -> bytes
  async def synthesize_stream(text, voice_id, **kwargs) -> AsyncIterator[bytes]

不选的 Provider 不会实例化，零内存开销。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Any

logger = logging.getLogger("navi.tts")


@dataclass
class TTSVoice:
    """一个可用的声音。"""
    id: str               # provider 内部 ID（如 edge-tts 的 "zh-CN-XiaoxiaoNeural"）
    name: str             # 显示名称
    language: str = "zh"  # 语言
    preview_url: str = "" # 试听链接（可选）


@dataclass
class TTSConfig:
    """单个 Provider 的配置。"""
    enabled: bool = False
    api_key: str = ""
    api_base: str = ""         # 自定义 endpoint（GPT-SoVITS / 兼容 API）
    voice_id: str = ""         # 默认 voice
    model: str = ""            # provider 特定的模型名
    refer_wav_path: str = ""   # GPT-SoVITS: 参考音频路径
    refer_prompt_text: str = ""  # GPT-SoVITS: 参考音频文本
    speed: float = 1.0
    extra: dict = field(default_factory=dict)  # provider 特定的额外参数


class TTSProvider(ABC):
    """TTS Provider 抽象基类。"""

    name: str = ""           # provider 标识符
    display_name: str = ""   # 显示名称
    supports_clone: bool = False  # 是否支持声音克隆
    is_local: bool = False   # 是否本地模型

    def __init__(self, config: TTSConfig):
        self.config = config

    @abstractmethod
    async def synthesize(self, text: str, voice_id: str = "", **kwargs) -> bytes:
        """文本 → 完整音频 bytes (wav/mp3)。"""
        ...

    async def synthesize_stream(self, text: str, voice_id: str = "", **kwargs) -> AsyncIterator[bytes]:
        """文本 → 流式音频 chunks。默认实现退化为一次性返回。"""
        data = await self.synthesize(text, voice_id, **kwargs)
        yield data

    async def list_voices(self) -> list[TTSVoice]:
        """列出此 Provider 可用的声音。"""
        return []

    async def health_check(self) -> bool:
        """检查 Provider 是否可用。"""
        try:
            await self.synthesize("测试", self.config.voice_id or "default")
            return True
        except Exception:
            return False


# ── Provider 注册表 ──────────────────────────────────────────────────────

_PROVIDER_CLASSES: dict[str, type[TTSProvider]] = {}


def register_tts_provider(name: str):
    """装饰器：注册 TTS Provider 类。"""
    def decorator(cls: type[TTSProvider]):
        cls.name = name
        _PROVIDER_CLASSES[name] = cls
        return cls
    return decorator


def get_tts_provider(name: str, config: TTSConfig) -> TTSProvider:
    """按名称实例化 Provider。仅在调用时加载，未选中的 Provider 零开销。"""
    if name not in _PROVIDER_CLASSES:
        # 延迟导入所有 provider 模块
        _ensure_providers_loaded()
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ValueError(f"Unknown TTS provider: {name}. Available: {list(_PROVIDER_CLASSES.keys())}")
    return cls(config)


def list_tts_providers() -> list[dict[str, Any]]:
    """列出所有已注册的 Provider 信息。"""
    _ensure_providers_loaded()
    result = []
    for name, cls in _PROVIDER_CLASSES.items():
        result.append({
            "name": name,
            "display_name": cls.display_name,
            "supports_clone": cls.supports_clone,
            "is_local": cls.is_local,
        })
    return result


_loaded = False

def _ensure_providers_loaded():
    """延迟加载所有 provider 模块（仅首次调用）。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    # 导入所有 provider 模块，触发 @register_tts_provider 装饰器
    try:
        from tts import edge_tts_provider  # noqa: F401
    except Exception as e:
        logger.debug(f"edge_tts provider not available: {e}")
    try:
        from tts import fish_audio_provider  # noqa: F401
    except Exception as e:
        logger.debug(f"fish_audio provider not available: {e}")
    try:
        from tts import openai_provider  # noqa: F401
    except Exception as e:
        logger.debug(f"openai_tts provider not available: {e}")
    try:
        from tts import gptsovits_provider  # noqa: F401
    except Exception as e:
        logger.debug(f"gptsovits provider not available: {e}")
