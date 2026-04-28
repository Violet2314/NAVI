"""TTS 服务管理器 — 管理当前 Provider 实例 + 配置持久化。

单例模式：全局唯一 TTSManager，按需实例化 Provider。
配置存储在 ~/.navi/config.yaml 的 tts 字段。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import asdict
from typing import Any

from tts.base import TTSConfig, TTSProvider, get_tts_provider, list_tts_providers

logger = logging.getLogger("navi.tts")


class TTSManager:
    """TTS 服务管理器（单例）。"""

    def __init__(self):
        self._provider: TTSProvider | None = None
        self._provider_name: str = ""
        self._config: dict[str, Any] = {}
        self._enabled: bool = False
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def load_config(self, tts_config: dict[str, Any]):
        """从 config.yaml 加载 TTS 配置。"""
        self._config = tts_config
        self._enabled = tts_config.get("enabled", False)
        self._provider_name = tts_config.get("provider", "edge_tts")
        # 清空已有 provider 实例，下次调用时重新创建
        self._provider = None

    def _get_provider_config(self) -> TTSConfig:
        """从 config dict 构建 TTSConfig。"""
        pc = self._config.get("providers", {}).get(self._provider_name, {})
        return TTSConfig(
            enabled=self._enabled,
            api_key=pc.get("api_key", ""),
            api_base=pc.get("api_base", ""),
            voice_id=pc.get("voice_id", ""),
            model=pc.get("model", ""),
            refer_wav_path=pc.get("refer_wav_path", ""),
            refer_prompt_text=pc.get("refer_prompt_text", ""),
            speed=pc.get("speed", 1.0),
            extra=pc.get("extra", {}),
        )

    def _ensure_provider(self) -> TTSProvider:
        """懒加载 Provider 实例。"""
        if self._provider is None:
            config = self._get_provider_config()
            self._provider = get_tts_provider(self._provider_name, config)
            logger.info(f"TTS Provider loaded: {self._provider_name}")
        return self._provider

    async def synthesize(self, text: str, voice_id: str = "", **kwargs) -> bytes:
        """合成语音。"""
        if not self._enabled:
            raise RuntimeError("TTS 未启用，请在设置中开启")
        provider = self._ensure_provider()
        return await provider.synthesize(text, voice_id, **kwargs)

    async def synthesize_to_base64(self, text: str, voice_id: str = "", **kwargs) -> str:
        """合成语音并返回 base64 编码（方便 WebSocket 传输）。"""
        data = await self.synthesize(text, voice_id, **kwargs)
        return base64.b64encode(data).decode("ascii")

    async def list_voices(self) -> list[dict]:
        """列出当前 Provider 的可用声音。"""
        if not self._enabled:
            return []
        provider = self._ensure_provider()
        voices = await provider.list_voices()
        return [{"id": v.id, "name": v.name, "language": v.language} for v in voices]

    async def health_check(self) -> dict[str, Any]:
        """检查 TTS 服务状态。"""
        if not self._enabled:
            return {"enabled": False, "provider": "", "healthy": False}
        try:
            provider = self._ensure_provider()
            healthy = await provider.health_check()
            return {
                "enabled": True,
                "provider": self._provider_name,
                "display_name": provider.display_name,
                "healthy": healthy,
            }
        except Exception as e:
            return {
                "enabled": True,
                "provider": self._provider_name,
                "healthy": False,
                "error": str(e),
            }

    def get_full_config(self) -> dict[str, Any]:
        """获取完整 TTS 配置（给前端用）。"""
        return {
            "enabled": self._enabled,
            "provider": self._provider_name,
            "providers": self._config.get("providers", {}),
            "available_providers": list_tts_providers(),
        }

    def update_config(self, new_config: dict[str, Any]) -> dict[str, Any]:
        """更新 TTS 配置并持久化到 config.yaml。"""
        from config import get_config
        import yaml

        # 更新内存状态
        self._enabled = new_config.get("enabled", self._enabled)
        new_provider = new_config.get("provider", self._provider_name)

        # 如果 provider 变了，清空实例
        if new_provider != self._provider_name:
            self._provider = None
            self._provider_name = new_provider

        # 合并 providers 子配置
        if "providers" in new_config:
            if "providers" not in self._config:
                self._config["providers"] = {}
            self._config["providers"].update(new_config["providers"])

        self._config["enabled"] = self._enabled
        self._config["provider"] = self._provider_name

        # 如果 provider 配置内容变了（api_key 等），也要清空重建
        self._provider = None

        # 写入 config.yaml
        try:
            cfg = get_config()
            from config.navi import NAVI_HOME
            config_path = NAVI_HOME / "config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            raw["tts"] = self._config
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
            logger.info("TTS config saved to config.yaml")
        except Exception as e:
            logger.warning(f"Failed to persist TTS config: {e}")

        return self.get_full_config()


# ── 全局单例 ─────────────────────────────────────────────────────────────

_manager: TTSManager | None = None


def get_tts_manager() -> TTSManager:
    """获取全局 TTS 管理器。"""
    global _manager
    if _manager is None:
        _manager = TTSManager()
        # 尝试从 config.yaml 加载初始配置
        try:
            from config import get_config
            cfg = get_config()
            tts_cfg = getattr(cfg, "tts", None)
            if isinstance(tts_cfg, dict):
                _manager.load_config(tts_cfg)
            else:
                # config.yaml 中没有 tts 字段，用默认值
                _manager.load_config({
                    "enabled": False,
                    "provider": "edge_tts",
                    "providers": {},
                })
        except Exception as e:
            logger.debug(f"Loading TTS config failed (using defaults): {e}")
            _manager.load_config({
                "enabled": False,
                "provider": "edge_tts",
                "providers": {},
            })
    return _manager
