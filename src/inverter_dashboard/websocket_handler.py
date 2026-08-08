"""
WebSocket handler for real-time dashboard updates
"""

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from . import ha_client
from .version import VERSION
from .config import DEFAULT_POWER_MIN, DEFAULT_POWER_MAX, DEFAULT_LOOP_INTERVAL, CONSOLE_SEND_LINES

logger = logging.getLogger(__name__)

# Connected WebSocket clients
ws_clients: set[WebSocket] = set()


# Pydantic model for allowed state fields - replaces _STATE_ALLOWLIST
# All fields optional since MQTT may not send all at once
class InverterState(BaseModel):
    """Validated inverter state payload sent to WebSocket clients."""

    model_config = ConfigDict(extra="ignore")  # silently drop unknown fields

    # Grid
    gt: float | int | None = None
    g1: float | int | None = None
    g2: float | int | None = None

    # Consumption
    tt: float | int | None = None
    t1: float | int | None = None
    t2: float | int | None = None

    # Solar
    solar_total: float | int | None = None
    mppt_total: float | int | None = None
    tasmota_total: float | int | None = None

    # Battery
    battery_soc: float | int | None = None
    battery_power: float | int | None = None
    battery_voltage: float | int | None = None
    battery_current: float | int | None = None

    # Inverter
    setpoint: float | int | None = None
    inverter_state: str | None = None
    version: str | None = None

    # Dashboard
    dashboard_version: str | None = None
    latest_version: str | None = None
    uptime: float | int | None = None

    # HA
    ha_connected: bool | None = None
    ha_direct_connected: bool | None = None

    # Control
    dry_run: bool | str | None = None
    ess_mode: dict[str, Any] | None = None

    # Feature flags / derived
    booleans: dict[str, bool] | None = None
    features: dict[str, bool] | None = None
    mppt_individual: list[float | int] | None = None
    tasmota_individual: list[float | int] | None = None
    mppt_chargers: list[dict[str, Any]] | None = None
    batteries: list[dict[str, Any]] | None = None
    loads: dict[str, float | int] | None = None
    ui_config: dict[str, Any] | None = None
    daily_stats: dict[str, Any] | None = None

    # EV
    ev_charging_kw: float | int | None = None
    ev_power: float | int | None = None
    car_soc: float | int | None = None

    # Water
    water_level: float | int | None = None
    water_valve: bool | str | None = None
    pump_switch: bool | str | None = None

    # Appliances
    dishwasher_running: bool | None = None
    dishwasher_duration: float | int | None = None
    washer_time: float | int | None = None
    washer_power: float | int | bool | None = None
    dryer_time: float | int | None = None
    dryer_power: float | int | bool | None = None

    # Console
    console: list[str] | None = None


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
    raw_state = ha_client.merge_overlay(mqtt.get_state())

    # Use Pydantic model to filter/validate - extra="ignore" drops unknown keys
    validated = InverterState(**raw_state)

    # Convert to dict, excluding None values for cleaner JSON
    filtered = validated.model_dump(exclude_none=True)

    return _with_ui_config(
        {
            **filtered,
            "console": mqtt.get_console()[-CONSOLE_SEND_LINES:],
            "dashboard_version": VERSION,
            "latest_version": _state["latest_version"],
        }
    )


def _with_ui_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge local_config-derived ui_config (e.g. home_buttons) into payload."""
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
        await mqtt_client.publish("toggle", {"entity": entity})
    elif action == "press":
        await mqtt_client.publish("press", {"entity": data.get("entity")})
    elif action == "setpoint":
        await mqtt_client.publish("setpoint", {"value": data.get("value")})
    elif action == "dry_run":
        await mqtt_client.publish("dry_run", {})
    elif action == "limits":
        await mqtt_client.publish(
            "limits",
            {
                "min": data.get("min", DEFAULT_POWER_MIN),
                "max": data.get("max", DEFAULT_POWER_MAX),
            },
        )
    elif action == "ess_mode":
        await mqtt_client.publish("ess_mode", {})
    elif action == "loop_interval":
        await mqtt_client.publish(
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
