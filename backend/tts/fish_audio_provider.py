"""Fish Audio TTS Provider — 云端声音克隆，二次元角色语音首选。

https://fish.audio
- 免费额度 $10/月
- 支持声音克隆（上传参考音频即可）
- 社区共享大量二次元 / 游戏角色模型
- 流式输出，首包 < 500ms
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Any

import httpx

from tts.base import TTSConfig, TTSProvider, TTSVoice, register_tts_provider

logger = logging.getLogger("navi.tts.fish_audio")

FISH_AUDIO_API = "https://api.fish.audio"


@register_tts_provider("fish_audio")
class FishAudioProvider(TTSProvider):
    """Fish Audio — 云端声音克隆 TTS。"""

    display_name = "Fish Audio（声音克隆）"
    supports_clone = True
    is_local = False

    async def synthesize(self, text: str, voice_id: str = "", **kwargs) -> bytes:
        """文本 → 完整音频 bytes。"""
        api_base = self.config.api_base or FISH_AUDIO_API
        api_key = self.config.api_key
        if not api_key:
            raise ValueError("Fish Audio 需要 API Key（去 fish.audio 注册获取）")

        reference_id = voice_id or self.config.voice_id
        payload: dict[str, Any] = {
            "text": text,
            "format": "mp3",
            "latency": "normal",
        }
        if reference_id:
            payload["reference_id"] = reference_id

        # 速度
        speed = kwargs.get("speed", self.config.speed)
        if speed != 1.0:
            payload["prosody"] = {"speed": speed}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{api_base}/v1/tts",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            return resp.content

    async def synthesize_stream(self, text: str, voice_id: str = "", **kwargs) -> AsyncIterator[bytes]:
        """流式输出音频。"""
        api_base = self.config.api_base or FISH_AUDIO_API
        api_key = self.config.api_key
        if not api_key:
            raise ValueError("Fish Audio 需要 API Key")

        reference_id = voice_id or self.config.voice_id
        payload: dict[str, Any] = {
            "text": text,
            "format": "mp3",
            "latency": "normal",
        }
        if reference_id:
            payload["reference_id"] = reference_id

        speed = kwargs.get("speed", self.config.speed)
        if speed != 1.0:
            payload["prosody"] = {"speed": speed}

        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{api_base}/v1/tts",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    yield chunk

    async def list_voices(self) -> list[TTSVoice]:
        """从 Fish Audio 获取可用的声音模型。"""
        api_base = self.config.api_base or FISH_AUDIO_API
        api_key = self.config.api_key
        if not api_key:
            return []

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{api_base}/model",
                    headers={"Authorization": f"Bearer {api_key}"},
                    params={"page_size": 20, "sort_by": "task_count"},
                )
                resp.raise_for_status()
                data = resp.json()
                voices = []
                for item in data.get("items", []):
                    voices.append(TTSVoice(
                        id=item["_id"],
                        name=item.get("title", item["_id"]),
                        language=",".join(item.get("languages", ["zh"])),
                    ))
                return voices
        except Exception as e:
            logger.warning(f"Failed to list Fish Audio voices: {e}")
            return []

    async def health_check(self) -> bool:
        if not self.config.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.config.api_base or FISH_AUDIO_API}/model",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    params={"page_size": 1},
                )
                return resp.status_code == 200
        except Exception:
            return False
