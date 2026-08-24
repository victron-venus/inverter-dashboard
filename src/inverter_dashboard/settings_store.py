"""
Dashboard settings persistence (JSON file next to local_config).

Covers runtime-editable UI settings (section visibility + camera topic)
and connection overrides (MQTT host/port/credentials, HA URL/token) that
take effect at next startup — env < dashboard_settings.json < CLI flag.
Secrets are stored plaintext in the settings file (same trust level as
local_config.py); GET /api/settings masks them.
"""

import json
import logging
import os
from typing import Any

from . import config
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
    # Connection overrides (applied at startup; restart required)
    "mqtt_host": str,
    "mqtt_port": int,
    "mqtt_username": str,
    "mqtt_password": str,
    "ha_url": str,
    "ha_token": str,
}

# Settings that must be masked in API responses.
SECRET_KEYS = ("mqtt_password", "ha_token")

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
    "mqtt_host": config.MQTT_HOST,
    "mqtt_port": config.MQTT_PORT,
    "mqtt_username": config.MQTT_USERNAME,
    "mqtt_password": "",
    "ha_url": "",
    "ha_token": "",
}

# Connection keys → config module attribute (mqtt_*) or ha_client override (ha_*)
_CONFIG_ATTRS = {
    "mqtt_host": "MQTT_HOST",
    "mqtt_port": "MQTT_PORT",
    "mqtt_username": "MQTT_USERNAME",
    "mqtt_password": "MQTT_PASSWORD",
}


def settings_path() -> str:
    """dashboard_settings.json lives where local_config.py is looked up."""
    env_dir = os.environ.get("INVERTER_DASHBOARD_CONFIG", "").strip()
    if env_dir:
        return os.path.join(env_dir, "dashboard_settings.json")
    repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(repo_root, "dashboard_settings.json")


def load_settings(mask_secrets: bool = False) -> dict[str, Any]:
    """Defaults overlaid with the persisted file (unknown/invalid entries ignored).

    mask_secrets=True replaces secret values with "***" (set vs empty only).
    """
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
    if mask_secrets:
        for k in SECRET_KEYS:
            out[k] = "***" if out.get(k) else ""
    return out


def apply_connection_overrides() -> int:
    """Apply persisted connection overrides to config + ha_client at startup.

    Only non-empty stored values win over env defaults. Returns count applied.
    """
    from . import ha_client

    applied = 0
    for key, attr in _CONFIG_ATTRS.items():
        val = load_settings().get(key)
        if val not in (None, "") and val != DEFAULTS.get(key):
            setattr(config, attr, val)
            applied += 1
    ha_url = load_settings().get("ha_url")
    ha_token = load_settings().get("ha_token")
    if ha_url or ha_token:
        ha_client.override_credentials(url=ha_url or None, token=ha_token or None)
        applied += 1
    if applied:
        logger.info("Applied %d connection override(s) from settings file", applied)
    return applied


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
