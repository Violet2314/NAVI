"""
Navi 配置管理（原 backend/config.py，已迁移至 config 包）
首次启动自动生成 ~/.navi/config.yaml
"""
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from functools import lru_cache

NAVI_HOME = Path.home() / ".navi"

DEFAULT_CONFIG = {
    "device_id": "home_pc",
    "data_dir": str(NAVI_HOME / "data"),
    "screenshot_dir": str(NAVI_HOME / "data" / "screenshots"),
    "memory_dir": str(NAVI_HOME / "memory"),

    "llm": {
        "vision_provider": "doubao",
        "vision_model": "doubao-vision-pro",
        "vision_api_key": "",
        "vision_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "text_provider": "deepseek",
        "text_model": "deepseek-chat",
        "text_api_key": "",
        "text_base_url": "https://api.deepseek.com",
    },

    "reports": {
        "output_dir": str(Path.home() / "obsidian"),
        "filename_format": "{date}-daily.md",
        "append_to_diary": False,
        "generate_time": "22:00",
    },

    "collector": {
        "window_interval_sec": 60,
        "screenshot_interval_sec": 30,
        "screenshot_similarity": 10,
        "screenshot_format": "jpg",
        "screenshot_quality": 75,
        "same_category_threshold_min": 5,
        "conflict_threshold_min": 3,
        "idle_threshold_sec": 300,
        "screenshot_keep_days": 3,
        "camera_enabled": False,
        "camera_index": 0,
        "camera_interval_sec": 5,
        "emotion_every_n": 6,
        "camera_confidence_threshold": 0.7,
    },

    "feishu": {
        "app_id": "",
        "app_secret": "",
        "webhook_url": "",
    },

    "sync": {
        "port": 8765,
    },

    "tts": {
        "enabled": False,
        "provider": "edge_tts",
        "providers": {
            "edge_tts": {
                "voice_id": "zh-CN-XiaoxiaoNeural",
                "speed": 1.0,
            },
            "fish_audio": {
                "api_key": "",
                "voice_id": "",
                "speed": 1.0,
            },
            "openai_tts": {
                "api_key": "",
                "api_base": "",
                "voice_id": "nova",
                "model": "tts-1",
                "speed": 1.0,
            },
            "gptsovits": {
                "api_base": "http://localhost:9880",
                "refer_wav_path": "",
                "refer_prompt_text": "",
                "speed": 1.0,
            },
        },
    },
}


@dataclass
class NaviConfig:
    device_id: str = "home_pc"
    data_dir: str = str(NAVI_HOME / "data")
    screenshot_dir: str = str(NAVI_HOME / "data" / "screenshots")
    memory_dir: str = str(NAVI_HOME / "memory")
    llm: dict = field(default_factory=dict)
    reports: dict = field(default_factory=dict)
    collector: dict = field(default_factory=dict)
    feishu: dict = field(default_factory=dict)
    sync: dict = field(default_factory=dict)
    tts: dict = field(default_factory=dict)


def _ensure_config_file() -> Path:
    config_path = NAVI_HOME / "config.yaml"
    if not config_path.exists():
        NAVI_HOME.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(DEFAULT_CONFIG, f, allow_unicode=True, default_flow_style=False)
        print(f"✅ 已生成默认配置文件：{config_path}")
        print("⚠️  请填写 LLM API Key 后重新启动")
    return config_path


@lru_cache(maxsize=1)
def get_config() -> NaviConfig:
    config_path = _ensure_config_file()
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # 合并默认值（防止配置文件缺字段）
    for key, val in DEFAULT_CONFIG.items():
        if key not in raw:
            raw[key] = val

    return NaviConfig(**{k: raw.get(k, DEFAULT_CONFIG.get(k)) for k in DEFAULT_CONFIG})
