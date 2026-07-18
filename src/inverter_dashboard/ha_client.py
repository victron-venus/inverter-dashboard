"""
Optional Home Assistant REST client for inverter-dashboard.

Reads site_config.py when present; if HA_DIRECT_CONTROLS is False or file missing,
all UI state for switches comes from MQTT (inverter-control) only.

When HA_DIRECT_CONTROLS is True and HA is configured, boolean/switch/water state for
entities listed in site_config comes only from HA REST polling — MQTT is not used as
fallback when HA is unreachable (values show off until HA responds again).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from .config import HA_POLL_TIMEOUT, HA_REQUEST_TIMEOUT

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
_switch_labels: Dict[str, str] = {}
# Dashboard keys -> HA entity IDs (washer/dishwasher telemetry omitted from MQTT when inverter-control uses MQTT_SLIM_STATE)
_appliance_entities: Dict[str, str] = {}

# Latest overlay merged into WebSocket payloads (replaced wholesale on each HA poll)
_overlay: Dict[str, Any] = {
    "booleans": {},
    "ha_direct_connected": False,
}

# Reusable async HTTP client for HA requests
_http_client: httpx.AsyncClient | None = None


def _prepend_site_config_import_path() -> None:
    """Load site_config from INVERTER_DASHBOARD_CONFIG (Docker mount) or app directory."""
    # Walk up from package dir to repo root (where site_config.py lives in dev/Docker)
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(pkg_dir, '..', '..'))

    candidates = []
    env_dir = os.environ.get("INVERTER_DASHBOARD_CONFIG", "").strip()
    if env_dir:
        candidates.append(env_dir)
    candidates.append(repo_root)
    candidates.append(pkg_dir)
    for d in candidates:
        if not d:
            continue
        path_py = os.path.join(d, "site_config.py")
        if os.path.isfile(path_py):
            if d not in sys.path:
                sys.path.insert(0, d)
            logger.info("Using site_config.py from %s", d)
            return


def _parse_ha_switch_entities(raw: Any) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Parse site_config.HA_SWITCH_ENTITIES: value may be entity_id str, (entity, label), or dict."""
    entities: Dict[str, str] = {}
    embedded_labels: Dict[str, str] = {}
    if not raw or not isinstance(raw, dict):
        return entities, embedded_labels
    for state_key, val in raw.items():
        if not state_key:
            continue
        if isinstance(val, str):
            entities[state_key] = val.strip()
        elif isinstance(val, (tuple, list)):
            if len(val) >= 1 and val[0]:
                entities[state_key] = str(val[0]).strip()
            if len(val) >= 2 and val[1]:
                embedded_labels[state_key] = str(val[1]).strip()
        elif isinstance(val, dict):
            eid = val.get("entity") or val.get("id") or val.get("entity_id")
            if eid:
                entities[state_key] = str(eid).strip()
            lab = val.get("label") or val.get("short") or val.get("name")
            if lab:
                embedded_labels[state_key] = str(lab).strip()
    return entities, embedded_labels


def _sensor_state_to_seconds(raw: Optional[str]) -> int:
    """Parse HA sensor state to seconds for dashboard timers (matches inverter-control numeric / HH:MM:SS)."""
    if raw in (None, "unavailable", "unknown", "None", ""):
        return 0
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        pass
    parts = str(raw).split(":")
    try:
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return h * 3600 + m * 60 + s
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return m * 60 + s
    except (ValueError, TypeError):
        pass
    return 0


def _boolish(raw: Optional[str]) -> bool:
    if raw is None:
        return False
    return str(raw).lower() in ("on", "true", "yes", "1")


def _appliance_field_value(state_key: str, entity_id: str, raw: Optional[str]) -> Any:
    """Map HA state string to dashboard type (bool vs seconds)."""
    domain = entity_id.split(".")[0]
    if domain in ("binary_sensor", "switch", "light", "input_boolean"):
        return _boolish(raw)
    if domain == "sensor":
        if state_key.endswith("_power"):
            try:
                return float(raw or 0) > 1.0
            except (ValueError, TypeError):
                return _boolish(raw)
        return _sensor_state_to_seconds(raw)
    return False


def _appliance_fallback(state_key: str) -> Any:
    if state_key.endswith("_time") or state_key.endswith("_duration"):
        return 0
    return False


def load_config():
    """Import site_config if present (see site_config.example.py)."""
    global _configured, _url, _token, _direct, _poll_interval
    global _boolean_entities, _switch_entities, _water_valve, _water_pump, _switch_labels
    global _appliance_entities

    _prepend_site_config_import_path()

    try:
        import site_config as sc  # type: ignore
    except ImportError:
        _configured = False
        _boolean_entities = {}
        _switch_entities = {}
        _switch_labels = {}
        _appliance_entities = {}
        logger.info("site_config.py not found — switch state from MQTT only")
        return

    _url = (getattr(sc, "HA_URL", "") or "").rstrip("/")
    _token = getattr(sc, "HA_TOKEN", "") or ""
    _direct = bool(getattr(sc, "HA_DIRECT_CONTROLS", False))
    _poll_interval = float(getattr(sc, "HA_POLL_INTERVAL_SEC", 12))

    _boolean_entities = dict(getattr(sc, "HA_BOOLEAN_ENTITIES", {}) or {})
    _sw_raw = getattr(sc, "HA_SWITCH_ENTITIES", {}) or {}
    _parsed_ent, _embedded_lab = _parse_ha_switch_entities(_sw_raw)
    _switch_entities = _parsed_ent
    _manual_lab = dict(getattr(sc, "HA_SWITCH_LABELS", {}) or {})
    _switch_labels = {**_embedded_lab, **_manual_lab}
    _water_valve = getattr(sc, "HA_WATER_VALVE_ENTITY", "") or ""
    _water_pump = getattr(sc, "HA_PUMP_SWITCH_ENTITY", "") or ""
    _appliance_entities = dict(getattr(sc, "HA_APPLIANCE_ENTITIES", {}) or {})

    _configured = bool(_url and _token and _token != "REPLACE_WITH_LONG_LIVED_ACCESS_TOKEN")
    if _direct and not _configured:
        logger.warning("HA_DIRECT_CONTROLS enabled but HA_URL/HA_TOKEN not set — falling back to MQTT")


def is_direct_mode() -> bool:
    return _configured and _direct


def _default_switch_label(state_key: str) -> str:
    """Human-readable label from state_key when HA_SWITCH_LABELS has no override."""
    s = state_key
    if s.startswith("home_"):
        s = s[5:]
    return s.replace("_", " ").upper()


def home_buttons_ui() -> List[Dict[str, Any]]:
    """Home card buttons: one row per HA_SWITCH_ENTITIES entry (order preserved)."""
    rows = []
    for state_key, entity_id in _switch_entities.items():
        label = _switch_labels.get(state_key) or _default_switch_label(state_key)
        btn_id = state_key.replace("_", "-")
        rows.append(
            {
                "id": btn_id,
                "label": label,
                "entity": entity_id,
                "state_key": state_key,
            }
        )
    return rows


def ui_config_patch() -> Dict[str, Any]:
    """Partial ui_config from site_config (merged into WebSocket payloads)."""
    if not _switch_entities:
        return {}
    return {"home_buttons": home_buttons_ui()}


def is_toggle_allowed(entity_id: str) -> bool:
    """Only entity IDs listed in site_config may be toggled from the dashboard."""
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


def _ha_headers() -> dict:
    """Common headers for HA REST API calls."""
    return {
        "Authorization": f"Bearer {_token}",
        "Content-Type": "application/json",
    }


def _get_http_client() -> httpx.AsyncClient:
    """Get or create a shared async HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=HA_REQUEST_TIMEOUT)
    return _http_client


async def _ha_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    timeout: float | None = None,
) -> httpx.Response | None:
    """Single helper for all HA REST calls. Returns response or None on error."""
    import asyncio

    if not _configured:
        return None
    client = _get_http_client()
    timeout_val = timeout or HA_REQUEST_TIMEOUT
    try:
        async with asyncio.timeout(timeout_val):
            resp = await client.request(
                method,
                f"{_url}{path}",
                headers=_ha_headers(),
                json=json_body,
            )
        return resp
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.exception("HA request %s %s failed: %s", method, path, e)
        return None


async def _get_state(client: httpx.AsyncClient, headers: dict, entity_id: str) -> Optional[str]:
    """GET /api/states/{entity_id} → state string."""
    safe = quote(entity_id, safe="")
    try:
        r = await client.get(f"{_url}/api/states/{safe}", headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("state")
    except (httpx.HTTPError, json.JSONDecodeError):
        return None


async def fetch_states_once() -> Dict[str, Any]:
    """Fetch all configured entities; returns overlay dict for merging into MQTT state."""
    if not is_direct_mode():
        return {}

    headers = _ha_headers()
    out: Dict[str, Any] = {"booleans": {}}

    try:
        async with httpx.AsyncClient(timeout=HA_POLL_TIMEOUT) as client:
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

            for key, eid in _appliance_entities.items():
                st = await _get_state(client, headers, eid)
                out[key] = _appliance_field_value(key, eid, st)

            out["ha_direct_connected"] = True
            return out

    except httpx.HTTPError as e:
        logger.warning("HA poll failed: %s", e)
        return {"booleans": {}, "ha_direct_connected": False}


def merge_overlay(base: Dict[str, Any]) -> Dict[str, Any]:
    """Merge MQTT state with HA overlay; in direct mode HA-owned keys never fall back to MQTT."""
    merged = dict(base)
    merged.setdefault("booleans", {})
    if not is_direct_mode():
        return merged

    o = _overlay
    connected = bool(o.get("ha_direct_connected"))
    merged["ha_direct_connected"] = connected

    if connected:
        merged["booleans"] = dict(o.get("booleans") or {})
        for k in _switch_entities:
            merged[k] = bool(o.get(k))
        if _water_valve:
            merged["water_valve"] = bool(o.get("water_valve"))
        if _water_pump:
            merged["pump_switch"] = bool(o.get("pump_switch"))
        for k in _appliance_entities:
            if k in o:
                merged[k] = o[k]
    else:
        merged["booleans"] = {k: False for k in _boolean_entities}
        for k in _switch_entities:
            merged[k] = False
        if _water_valve:
            merged["water_valve"] = False
        if _water_pump:
            merged["pump_switch"] = False
        for k in _appliance_entities:
            merged[k] = _appliance_fallback(k)

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
    if domain not in ("input_boolean", "switch", "light"):
        return False

    resp = await _ha_request("POST", f"/api/services/{domain}/{service}", json_body={"entity_id": entity_id})
    return resp is not None and resp.status_code == 200


async def toggle_entity(entity_id: str) -> bool:
    """HA toggle service (no prior state required)."""
    if not _configured:
        return False

    domain = entity_id.split(".")[0]
    if domain == "input_boolean":
        svc = "input_boolean/toggle"
    elif domain == "switch":
        svc = "switch/toggle"
    elif domain == "light":
        svc = "light/toggle"
    else:
        return False

    resp = await _ha_request("POST", f"/api/services/{svc}", json_body={"entity_id": entity_id})
    return resp is not None and resp.status_code == 200


def domain_for_press(entity_id: str) -> Optional[str]:
    if entity_id.startswith("button."):
        return "button"
    return None


async def press_entity(entity_id: str) -> bool:
    """Fire button.press."""
    resp = await _ha_request("POST", "/api/services/button/press", json_body={"entity_id": entity_id})
    return resp is not None and resp.status_code == 200
