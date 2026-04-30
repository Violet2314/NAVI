"""GPT-SoVITS 本地 Provider — 本地声音克隆 TTS（需 GPU）。

需要本地部署 GPT-SoVITS 的 API 服务：
  https://github.com/RVC-Boss/GPT-SoVITS
启动后默认监听 http://localhost:9880

支持：
  - 零样本声音克隆（传参考音频 wav + 参考文本）
  - 微调模型（加载训练好的权重）
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

from tts.base import TTSConfig, TTSProvider, TTSVoice, register_tts_provider

logger = logging.getLogger("navi.tts.gptsovits")


@register_tts_provider("gptsovits")
class GPTSoVITSProvider(TTSProvider):
    """GPT-SoVITS 本地部署 — 声音克隆 TTS。"""

    display_name = "GPT-SoVITS（本地部署）"
    supports_clone = True
    is_local = True

    async def synthesize(self, text: str, voice_id: str = "", **kwargs) -> bytes:
        api_base = (self.config.api_base or "http://localhost:9880").rstrip("/")

        payload = {
            "text": text,
            "text_language": kwargs.get("language", "zh"),
        }

        # 参考音频（声音克隆核心）
        refer_wav = kwargs.get("refer_wav_path", self.config.refer_wav_path)
        refer_text = kwargs.get("refer_prompt_text", self.config.refer_prompt_text)
        if refer_wav:
            payload["refer_wav_path"] = refer_wav
            payload["prompt_text"] = refer_text or ""
            payload["prompt_language"] = kwargs.get("prompt_language", "zh")

        # 速度
        speed = kwargs.get("speed", self.config.speed)
        if speed != 1.0:
            payload["speed_factor"] = speed

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{api_base}/tts", json=payload)
                resp.raise_for_status()
                return resp.content
        except httpx.ConnectError:
            raise ConnectionError(
                f"无法连接 GPT-SoVITS 服务（{api_base}）。"
                f"请确认 GPT-SoVITS 已启动：python api.py"
            )

    async def synthesize_stream(self, text: str, voice_id: str = "", **kwargs) -> AsyncIterator[bytes]:
        """GPT-SoVITS 支持流式输出（v2 API）。"""
        api_base = (self.config.api_base or "http://localhost:9880").rstrip("/")

        payload = {
            "text": text,
            "text_language": kwargs.get("language", "zh"),
            "streaming_mode": True,
        }

        refer_wav = kwargs.get("refer_wav_path", self.config.refer_wav_path)
        refer_text = kwargs.get("refer_prompt_text", self.config.refer_prompt_text)
        if refer_wav:
            payload["refer_wav_path"] = refer_wav
            payload["prompt_text"] = refer_text or ""
            payload["prompt_language"] = kwargs.get("prompt_language", "zh")

        speed = kwargs.get("speed", self.config.speed)
        if speed != 1.0:
            payload["speed_factor"] = speed

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", f"{api_base}/tts", json=payload) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=4096):
                        yield chunk
        except httpx.ConnectError:
            raise ConnectionError(
                f"无法连接 GPT-SoVITS 服务（{api_base}）。"
            )

    async def list_voices(self) -> list[TTSVoice]:
        """GPT-SoVITS 没有预设 voice 列表，靠参考音频切换。"""
        return [
            TTSVoice(id="default", name="默认（使用参考音频）", language="zh"),
        ]

    async def health_check(self) -> bool:
        api_base = (self.config.api_base or "http://localhost:9880").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{api_base}/")
                return resp.status_code < 500
        except Exception:
            return False
