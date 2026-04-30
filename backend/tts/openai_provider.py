"""OpenAI TTS Provider — OpenAI 官方 / 兼容 API。

支持 tts-1, tts-1-hd, gpt-4o-mini-tts 等模型。
也兼容任何 OpenAI-compatible TTS endpoint（如 Groq、SiliconFlow 等）。
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

from tts.base import TTSConfig, TTSProvider, TTSVoice, register_tts_provider

logger = logging.getLogger("navi.tts.openai")

OPENAI_VOICES = [
    TTSVoice(id="alloy", name="Alloy（中性）", language="multi"),
    TTSVoice(id="ash", name="Ash（男）", language="multi"),
    TTSVoice(id="coral", name="Coral（女）", language="multi"),
    TTSVoice(id="echo", name="Echo（男）", language="multi"),
    TTSVoice(id="fable", name="Fable（男）", language="multi"),
    TTSVoice(id="nova", name="Nova（女·推荐）", language="multi"),
    TTSVoice(id="onyx", name="Onyx（男·深沉）", language="multi"),
    TTSVoice(id="sage", name="Sage（中性）", language="multi"),
    TTSVoice(id="shimmer", name="Shimmer（女）", language="multi"),
]


@register_tts_provider("openai_tts")
class OpenAITTSProvider(TTSProvider):
    """OpenAI TTS / OpenAI-compatible TTS API。"""

    display_name = "OpenAI TTS"
    supports_clone = False
    is_local = False

    async def synthesize(self, text: str, voice_id: str = "", **kwargs) -> bytes:
        api_base = (self.config.api_base or "https://api.openai.com").rstrip("/")
        api_key = self.config.api_key
        if not api_key:
            raise ValueError("OpenAI TTS 需要 API Key")

        voice = voice_id or self.config.voice_id or "nova"
        model = self.config.model or "tts-1"
        speed = kwargs.get("speed", self.config.speed)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{api_base}/v1/audio/speech",
                json={
                    "model": model,
                    "input": text,
                    "voice": voice,
                    "response_format": "mp3",
                    "speed": speed,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            return resp.content

    async def synthesize_stream(self, text: str, voice_id: str = "", **kwargs) -> AsyncIterator[bytes]:
        api_base = (self.config.api_base or "https://api.openai.com").rstrip("/")
        api_key = self.config.api_key
        if not api_key:
            raise ValueError("OpenAI TTS 需要 API Key")

        voice = voice_id or self.config.voice_id or "nova"
        model = self.config.model or "tts-1"
        speed = kwargs.get("speed", self.config.speed)

        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{api_base}/v1/audio/speech",
                json={
                    "model": model,
                    "input": text,
                    "voice": voice,
                    "response_format": "mp3",
                    "speed": speed,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    yield chunk

    async def list_voices(self) -> list[TTSVoice]:
        return OPENAI_VOICES

    async def health_check(self) -> bool:
        if not self.config.api_key:
            return False
        try:
            data = await self.synthesize("hello", "alloy")
            return len(data) > 100
        except Exception as e:
            logger.warning(f"OpenAI TTS health check failed: {e}")
            return False
