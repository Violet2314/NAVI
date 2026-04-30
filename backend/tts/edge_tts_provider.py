"""Edge TTS Provider — 微软免费 TTS，零资源占用。

音色固定但自然度高，中文支持好。适合作为默认 / 保底方案。
pip install edge-tts
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import AsyncIterator

from tts.base import TTSConfig, TTSProvider, TTSVoice, register_tts_provider

logger = logging.getLogger("navi.tts.edge")

# 常用中文音色
EDGE_VOICES_ZH = [
    TTSVoice(id="zh-CN-XiaoxiaoNeural", name="晓晓（女·温暖）", language="zh"),
    TTSVoice(id="zh-CN-XiaoyiNeural", name="晓伊（女·活泼）", language="zh"),
    TTSVoice(id="zh-CN-YunjianNeural", name="云健（男·自然）", language="zh"),
    TTSVoice(id="zh-CN-YunxiNeural", name="云希（男·少年）", language="zh"),
    TTSVoice(id="zh-CN-YunxiaNeural", name="云夏（男·儿童）", language="zh"),
    TTSVoice(id="zh-CN-liaoning-XiaobeiNeural", name="晓北（女·东北）", language="zh"),
    TTSVoice(id="zh-TW-HsiaoChenNeural", name="晓辰（女·台湾）", language="zh"),
    TTSVoice(id="zh-TW-YunJheNeural", name="云哲（男·台湾）", language="zh"),
]

EDGE_VOICES_EN = [
    TTSVoice(id="en-US-JennyNeural", name="Jenny (Female)", language="en"),
    TTSVoice(id="en-US-GuyNeural", name="Guy (Male)", language="en"),
    TTSVoice(id="en-US-AriaNeural", name="Aria (Female)", language="en"),
]

EDGE_VOICES_JA = [
    TTSVoice(id="ja-JP-NanamiNeural", name="七海（女）", language="ja"),
    TTSVoice(id="ja-JP-KeitaNeural", name="圭太（男）", language="ja"),
]


@register_tts_provider("edge_tts")
class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge TTS — 免费，无需 API Key。"""

    display_name = "Edge TTS（免费）"
    supports_clone = False
    is_local = False

    async def synthesize(self, text: str, voice_id: str = "", **kwargs) -> bytes:
        """文本 → wav bytes。"""
        import edge_tts

        voice = voice_id or self.config.voice_id or "zh-CN-XiaoxiaoNeural"
        rate = self._speed_to_rate(kwargs.get("speed", self.config.speed))

        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    async def synthesize_stream(self, text: str, voice_id: str = "", **kwargs) -> AsyncIterator[bytes]:
        """流式输出音频 chunks。"""
        import edge_tts

        voice = voice_id or self.config.voice_id or "zh-CN-XiaoxiaoNeural"
        rate = self._speed_to_rate(kwargs.get("speed", self.config.speed))

        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]

    async def list_voices(self) -> list[TTSVoice]:
        return EDGE_VOICES_ZH + EDGE_VOICES_JA + EDGE_VOICES_EN

    async def health_check(self) -> bool:
        try:
            data = await self.synthesize("你好", "zh-CN-XiaoxiaoNeural")
            return len(data) > 100
        except Exception as e:
            logger.warning(f"Edge TTS health check failed: {e}")
            return False

    @staticmethod
    def _speed_to_rate(speed: float) -> str:
        """speed 1.0 → "+0%", speed 1.5 → "+50%", speed 0.8 → "-20%"。"""
        pct = int((speed - 1.0) * 100)
        return f"+{pct}%" if pct >= 0 else f"{pct}%"
