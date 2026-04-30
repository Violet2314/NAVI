"""Configuration module — navi agent config + Navi native config."""

# ── navi agent config ─────────────────────────────────────────────────────
from config.loader import get_config_path, load_config
from config.paths import (
    get_bridge_install_dir,
    get_cli_history_path,
    get_cron_dir,
    get_data_dir,
    get_legacy_sessions_dir,
    get_logs_dir,
    get_media_dir,
    get_runtime_subdir,
    get_workspace_path,
)
from config.schema import Config

# ── Navi native config ───────────────────────────────────────────────────────
from config.navi import NAVI_HOME, DEFAULT_CONFIG, NaviConfig, get_config

__all__ = [
    # navi
    "Config",
    "load_config",
    "get_config_path",
    "get_data_dir",
    "get_runtime_subdir",
    "get_media_dir",
    "get_cron_dir",
    "get_logs_dir",
    "get_workspace_path",
    "get_cli_history_path",
    "get_bridge_install_dir",
    "get_legacy_sessions_dir",
    # Navi
    "NAVI_HOME",
    "DEFAULT_CONFIG",
    "NaviConfig",
    "get_config",
]