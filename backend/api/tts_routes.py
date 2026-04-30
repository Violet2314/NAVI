"""TTS API 路由 — 配置管理 + 语音合成接口。

GET  /api/tts/config        → 获取 TTS 配置
POST /api/tts/config        → 更新 TTS 配置
GET  /api/tts/voices        → 列出当前 Provider 可用声音
POST /api/tts/synthesize    → 合成语音（返回 base64 音频）
GET  /api/tts/health        → 健康检查
GET  /api/tts/providers     → 列出所有可用 Provider
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("navi.tts.api")

tts_router = APIRouter(prefix="/api/tts", tags=["TTS"])


class SynthesizeRequest(BaseModel):
    text: str
    voice_id: str = ""
    speed: float = 1.0


@tts_router.get("/config")
async def get_tts_config():
    """获取 TTS 配置（Provider 列表 + 当前选中 + 各 Provider 配置）。"""
    from tts.manager import get_tts_manager
    mgr = get_tts_manager()
    return mgr.get_full_config()


@tts_router.post("/config")
async def update_tts_config(body: Dict[str, Any]):
    """更新 TTS 配置。"""
    from tts.manager import get_tts_manager
    mgr = get_tts_manager()
    try:
        result = mgr.update_config(body)
        return {"ok": True, "config": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@tts_router.get("/voices")
async def list_voices():
    """列出当前 Provider 的可用声音。"""
    from tts.manager import get_tts_manager
    mgr = get_tts_manager()
    try:
        voices = await mgr.list_voices()
        return {"voices": voices}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@tts_router.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    """合成语音 → 返回 base64 编码的音频。"""
    from tts.manager import get_tts_manager
    mgr = get_tts_manager()
    if not mgr.enabled:
        raise HTTPException(status_code=400, detail="TTS 未启用")
    try:
        audio_b64 = await mgr.synthesize_to_base64(
            text=req.text,
            voice_id=req.voice_id,
            speed=req.speed,
        )
        return {
            "audio": audio_b64,
            "format": "mp3",
            "provider": mgr.provider_name,
        }
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"TTS synthesize error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS 合成失败: {e}")


@tts_router.get("/health")
async def tts_health():
    """TTS 服务健康检查。"""
    from tts.manager import get_tts_manager
    mgr = get_tts_manager()
    return await mgr.health_check()


@tts_router.get("/providers")
async def list_providers():
    """列出所有可用的 TTS Provider。"""
    from tts.base import list_tts_providers
    return {"providers": list_tts_providers()}
