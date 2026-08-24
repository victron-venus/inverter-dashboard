"""
Dashboard settings persistence (JSON file next to local_config).

Covers runtime-editable settings: UI section visibility + camera topic.
Secrets (MQTT/HA credentials) intentionally stay in env/local_config —
they are not exposed or writable through this API.

Hot-applied keys take effect on the next broadcast; camera_topic needs
a process restart to re-subscribe.
"""

import json
import logging
import os
from typing import Any

from .config import CAMERA_TOPIC

logger = logging.getLogger(__name__)

# Keys the dashboard may read/write; everything else is rejected.
ALLOWED_KEYS = {
    "camera_topic": str,
    "show_ev": bool,
    "show_washer": bool,
    "show_dryer": bool,
    "show_dishwasher": bool,
    "show_home_section": bool,
    "show_ha_covers": bool,
    "show_ha_media": bool,
    "show_ha_scenes": bool,
    "show_ha_weather": bool,
}

DEFAULTS: dict[str, Any] = {
    "camera_topic": CAMERA_TOPIC,
    "show_ev": True,
    "show_washer": True,
    "show_dryer": True,
    "show_dishwasher": True,
    "show_home_section": True,
    "show_ha_covers": True,
    "show_ha_media": True,
    "show_ha_scenes": True,
    "show_ha_weather": True,
}


def settings_path() -> str:
    """dashboard_settings.json lives where local_config.py is looked up."""
    env_dir = os.environ.get("INVERTER_DASHBOARD_CONFIG", "").strip()
    if env_dir:
        return os.path.join(env_dir, "dashboard_settings.json")
    repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(repo_root, "dashboard_settings.json")


def load_settings() -> dict[str, Any]:
    """Defaults overlaid with the persisted file (unknown/invalid entries ignored)."""
    out = dict(DEFAULTS)
    try:
        with open(settings_path(), encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            for k, typ in ALLOWED_KEYS.items():
                if k in stored and isinstance(stored[k], typ):
                    out[k] = stored[k]
    except (OSError, json.JSONDecodeError):
        pass
    return out


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate patch against ALLOWED_KEYS, merge-write, return new settings.

    Raises ValueError on unknown keys or wrong types.
    """
    clean: dict[str, Any] = {}
    for k, v in patch.items():
        if k not in ALLOWED_KEYS:
            raise ValueError(f"unknown setting: {k}")
        if not isinstance(v, ALLOWED_KEYS[k]):
            # API contract: bad type is a 400 (ValueError), not a TypeError
            raise ValueError(f"setting {k} must be {ALLOWED_KEYS[k].__name__}")  # noqa: TRY004
        clean[k] = v

    merged = load_settings()
    merged.update(clean)
    tmp = settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    os.replace(tmp, settings_path())
    logger.info("Saved settings: %s", sorted(clean))
    return merged
