"""
Optional Home Assistant REST client for inverter-dashboard.

Reads ha_secrets.py when present; if HA_DIRECT_CONTROLS is False or file missing,
all UI state for switches comes from MQTT (inverter-control) only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_configured = False
_url = ""
_token = ""
_direct = False
_poll_interval = 12.0

_boolean_entities: Dict[str, str] = {}
_switch_entities: Dict[str, str] = {}
_water_valve = ""
_water_pump = ""

# Latest overlay merged into WebSocket payloads (replaced wholesale on each HA poll)
_overlay: Dict[str, Any] = {
    "booleans": {},
    "ha_direct_connected": False,
}


def _prepend_ha_secrets_import_path() -> None:
    """Load ha_secrets from INVERTER_DASHBOARD_CONFIG or ./config (Docker bind-mount)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    env_dir = os.environ.get("INVERTER_DASHBOARD_CONFIG", "").strip()
    if env_dir:
        candidates.append(env_dir)
    candidates.append(os.path.join(here, "config"))
    for d in candidates:
        if not d:
            continue
        path_py = os.path.join(d, "ha_secrets.py")
        if os.path.isfile(path_py):
            if d not in sys.path:
                sys.path.insert(0, d)
            logger.info("Using ha_secrets.py from %s", d)
            return


def load_config():
    """Import ha_secrets if present (see ha_secrets.example.py, config/ on host)."""
    global _configured, _url, _token, _direct, _poll_interval
    global _boolean_entities, _switch_entities, _water_valve, _water_pump

    _prepend_ha_secrets_import_path()

    try:
        import ha_secrets as hs  # type: ignore
    except ImportError:
        _configured = False
        logger.info("ha_secrets.py not found — switch state from MQTT only")
        return

    _url = (getattr(hs, "HA_URL", "") or "").rstrip("/")
    _token = getattr(hs, "HA_TOKEN", "") or ""
    _direct = bool(getattr(hs, "HA_DIRECT_CONTROLS", False))
    _poll_interval = float(getattr(hs, "HA_POLL_INTERVAL_SEC", 12))

    _boolean_entities = dict(getattr(hs, "HA_BOOLEAN_ENTITIES", {}) or {})
    _switch_entities = dict(getattr(hs, "HA_SWITCH_ENTITIES", {}) or {})
    _water_valve = getattr(hs, "HA_WATER_VALVE_ENTITY", "") or ""
    _water_pump = getattr(hs, "HA_PUMP_SWITCH_ENTITY", "") or ""

    _configured = bool(_url and _token and _token != "REPLACE_WITH_LONG_LIVED_ACCESS_TOKEN")
    if _direct and not _configured:
        logger.warning("HA_DIRECT_CONTROLS enabled but HA_URL/HA_TOKEN not set — falling back to MQTT")


def is_direct_mode() -> bool:
    return _configured and _direct


def is_toggle_allowed(entity_id: str) -> bool:
    """Only entity IDs listed in ha_secrets may be toggled from the dashboard."""
    if not entity_id or not _configured:
        return False
    allowed = set(_boolean_entities.values()) | set(_switch_entities.values())
    if _water_valve:
        allowed.add(_water_valve)
    if _water_pump:
        allowed.add(_water_pump)
    return entity_id in allowed


def replace_overlay(data: Dict[str, Any]) -> None:
    """Replace HA overlay (used after successful poll or toggle refresh)."""
    global _overlay
    _overlay = data


async def _get_state(client: httpx.AsyncClient, headers: dict, entity_id: str) -> Optional[str]:
    """GET /api/states/{entity_id} → state string."""
    from urllib.parse import quote

    safe = quote(entity_id, safe="")
    try:
        r = await client.get(f"{_url}/api/states/{safe}", headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("state")
    except Exception:
        return None


async def fetch_states_once() -> Dict[str, Any]:
    """Fetch all configured entities; returns overlay dict for merging into MQTT state."""
    if not is_direct_mode():
        return {}

    headers = {
        "Authorization": f"Bearer {_token}",
        "Content-Type": "application/json",
    }
    out: Dict[str, Any] = {"booleans": {}}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            booleans = {}
            for key, eid in _boolean_entities.items():
                st = await _get_state(client, headers, eid)
                booleans[key] = st == "on"
            out["booleans"] = booleans

            for key, eid in _switch_entities.items():
                st = await _get_state(client, headers, eid)
                out[key] = st == "on"

            if _water_valve:
                st = await _get_state(client, headers, _water_valve)
                out["water_valve"] = st == "on"
            if _water_pump:
                st = await _get_state(client, headers, _water_pump)
                out["pump_switch"] = st == "on"

            out["ha_direct_connected"] = True
            return out

    except Exception as e:
        logger.warning(f"HA poll failed: {e}")
        return {"booleans": {}, "ha_direct_connected": False}


def merge_overlay(base: Dict[str, Any]) -> Dict[str, Any]:
    """Merge MQTT state with last HA overlay when in direct mode."""
    merged = dict(base)
    merged.setdefault("booleans", {})
    if not is_direct_mode():
        return merged

    o = _overlay
    merged["ha_direct_connected"] = o.get("ha_direct_connected", False)
    if not o.get("ha_direct_connected"):
        return merged

    if o.get("booleans"):
        merged["booleans"] = dict(o["booleans"])
    for k in _switch_entities:
        if k in o:
            merged[k] = o[k]
    if _water_valve and "water_valve" in o:
        merged["water_valve"] = o["water_valve"]
    if _water_pump and "pump_switch" in o:
        merged["pump_switch"] = o["pump_switch"]
    return merged


async def ha_poll_loop():
    """Background task: refresh HA overlay periodically."""
    load_config()
    if not is_direct_mode():
        return

    while True:
        replace_overlay(await fetch_states_once())
        await asyncio.sleep(_poll_interval)


async def call_turn(entity_id: str, turn_on: bool) -> bool:
    """Explicit turn_on / turn_off."""
    if not _configured:
        return False

    domain = entity_id.split(".")[0]
    service = "turn_on" if turn_on else "turn_off"
    if domain not in ("input_boolean", "switch"):
        return False

    headers = {
        "Authorization": f"Bearer {_token}",
        "Content-Type": "application/json",
    }
    body = {"entity_id": entity_id}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_url}/api/services/{domain}/{service}",
                headers=headers,
                json=body,
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"HA service call failed: {e}")
        return False


async def toggle_entity(entity_id: str) -> bool:
    """HA toggle service (no prior state required)."""
    if not _configured:
        return False

    domain = entity_id.split(".")[0]
    if domain == "input_boolean":
        svc = "input_boolean/toggle"
    elif domain == "switch":
        svc = "switch/toggle"
    else:
        return False

    headers = {
        "Authorization": f"Bearer {_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_url}/api/services/{svc}",
                headers=headers,
                json={"entity_id": entity_id},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"HA toggle failed: {e}")
        return False


def domain_for_press(entity_id: str) -> Optional[str]:
    if entity_id.startswith("button."):
        return "button"
    return None


async def press_entity(entity_id: str) -> bool:
    """Fire button.press."""
    headers = {
        "Authorization": f"Bearer {_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_url}/api/services/button/press",
                headers=headers,
                json={"entity_id": entity_id},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"HA button press failed: {e}")
        return False
