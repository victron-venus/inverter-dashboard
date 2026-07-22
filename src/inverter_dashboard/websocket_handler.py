"""
WebSocket handler for real-time dashboard updates
"""

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from . import mqtt_handler
from . import ha_client
from .version import VERSION
from .config import DEFAULT_POWER_MIN, DEFAULT_POWER_MAX, DEFAULT_LOOP_INTERVAL, CONSOLE_SEND_LINES

logger = logging.getLogger(__name__)

# Connected WebSocket clients
ws_clients: set[WebSocket] = set()

# Keys from inverter/state that are safe to forward to WebSocket clients.
# Defense-in-depth: prevents accidental inclusion of sensitive fields.
_STATE_ALLOWLIST: set[str] = {
    "gt",
    "g1",
    "g2",
    "tt",
    "t1",
    "t2",
    "solar_total",
    "mppt_total",
    "tasmota_total",
    "battery_soc",
    "battery_power",
    "battery_voltage",
    "battery_current",
    "setpoint",
    "inverter_state",
    "version",
    "dashboard_version",
    "latest_version",
    "uptime",
    "ha_connected",
    "ha_direct_connected",
    "dry_run",
    "ess_mode",
    "booleans",
    "features",
    "mppt_individual",
    "tasmota_individual",
    "mppt_chargers",
    "batteries",
    "loads",
    "ui_config",
    "daily_stats",
    "ev_charging_kw",
    "ev_power",
    "car_soc",
    "water_level",
    "water_valve",
    "pump_switch",
    "dishwasher_running",
    "dishwasher_duration",
    "washer_time",
    "washer_power",
    "dryer_time",
    "dryer_power",
    "console",
}

# Mutable module-level state (avoids global statements)
_state: dict[str, Any] = {"latest_version": None, "mqtt_state": None}


def set_latest_version(version: str | None) -> None:
    """Update cached latest version"""
    _state["latest_version"] = version


def set_mqtt_state(mqtt_state):
    """Set the MqttState reference for state reads."""
    _state["mqtt_state"] = mqtt_state


def build_payload() -> dict[str, Any]:
    """Build the canonical state payload sent to all WebSocket clients."""
    mqtt = _state["mqtt_state"]
    state = ha_client.merge_overlay(mqtt.get_state())
    allowlist = _STATE_ALLOWLIST | ha_client.dynamic_state_keys()
    filtered = {k: v for k, v in state.items() if k in allowlist}
    return _with_ui_config(
        {
            **filtered,
            "console": mqtt.get_console()[-CONSOLE_SEND_LINES:],
            "dashboard_version": VERSION,
            "latest_version": _state["latest_version"],
        }
    )


def _with_ui_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge site_config-derived ui_config (e.g. home_buttons) into payload."""
    patch = ha_client.ui_config_patch()
    if not patch:
        return payload
    out = dict(payload)
    uc = dict(out.get("ui_config") or {})
    uc.update(patch)
    out["ui_config"] = uc
    return out


async def broadcast_state():
    """Send state to all WebSocket clients"""
    # Snapshot to avoid mutation during iteration
    clients = list(ws_clients)
    if not clients:
        return

    data = build_payload()
    message = json.dumps(data)
    disconnected: list[WebSocket] = []

    for ws in clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        ws_clients.discard(ws)


async def _dispatch_action(action: str, data: dict[str, Any], mqtt_client):
    """Dispatch a single WebSocket action."""
    if action == "toggle":
        entity = data.get("entity")
        if entity and ha_client.is_direct_mode() and ha_client.is_toggle_allowed(entity):
            await ha_client.toggle_entity(entity)
            fresh = await ha_client.fetch_states_once()
            if fresh.get("ha_direct_connected"):
                ha_client.replace_overlay(fresh)
            await broadcast_state()
            return
        mqtt_handler.publish_command(mqtt_client, "toggle", {"entity": entity})
    elif action == "press":
        mqtt_handler.publish_command(mqtt_client, "press", {"entity": data.get("entity")})
    elif action == "setpoint":
        mqtt_handler.publish_command(mqtt_client, "setpoint", {"value": data.get("value")})
    elif action == "dry_run":
        mqtt_handler.publish_command(mqtt_client, "dry_run", {})
    elif action == "limits":
        mqtt_handler.publish_command(
            mqtt_client,
            "limits",
            {
                "min": data.get("min", DEFAULT_POWER_MIN),
                "max": data.get("max", DEFAULT_POWER_MAX),
            },
        )
    elif action == "ess_mode":
        mqtt_handler.publish_command(mqtt_client, "ess_mode", {})
    elif action == "loop_interval":
        mqtt_handler.publish_command(
            mqtt_client,
            "loop_interval",
            {"interval": data.get("interval", DEFAULT_LOOP_INTERVAL)},
        )


async def handle_websocket(websocket: WebSocket, mqtt_client):
    """Handle WebSocket connection"""
    await websocket.accept()
    ws_clients.add(websocket)
    logger.info("WebSocket client connected (%d total)", len(ws_clients))

    try:
        await websocket.send_json(build_payload())

        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            if action:
                await _dispatch_action(action, data, mqtt_client)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
    finally:
        ws_clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(ws_clients))
