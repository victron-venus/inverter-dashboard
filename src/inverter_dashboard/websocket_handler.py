"""
WebSocket handler for real-time dashboard updates
"""

import json
import logging
from typing import Any

from aiomqtt import Client
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from . import config, gateway, ha_client, settings_store
from .cerbo import number
from .config import DEFAULT_LOOP_INTERVAL, DEFAULT_POWER_MAX, DEFAULT_POWER_MIN
from .version import VERSION

logger = logging.getLogger(__name__)


async def mqtt_publish(client: Client, action: str, payload: dict[str, Any] | None = None) -> None:
    """Publish command to inverter-control using aiomqtt Client."""
    if client is None:
        logger.warning("Cannot publish: MQTT client not connected")
        return

    topic = f"inverter/cmd/{action}"
    message = json.dumps(payload) if payload else ""

    try:
        await client.publish(topic, message, qos=0)
        logger.debug("Published command to %s", topic)
    except Exception:
        logger.exception("Failed to publish to %s", topic)


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
    g3: float | int | None = None

    # Consumption
    tt: float | int | None = None
    t1: float | int | None = None
    t2: float | int | None = None
    t3: float | int | None = None

    # False means unavailable, including an explicitly invalidated MQTT value.
    telemetry_available: dict[str, bool] | None = None
    grid_available: bool | None = None

    # Solar
    solar_total: float | int | None = None
    mppt_total: float | int | None = None
    pv_inverter_total: float | int | None = None

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
    limits: dict[str, float | int] | None = None
    loop_interval: float | int | None = None
    dvcc_limits: dict[str, Any] | None = None
    perf: dict[str, Any] | None = None

    # Controller/watchdog status remains in slim inverter/state. These are
    # policy diagnostics, separate from native grid measurement availability.
    grid_control_valid: bool | None = None
    grid_control_reason: str | None = None
    grid_loss_state: str | None = None
    grid_loss_hold_seconds: float | int | None = None
    grid_loss_elapsed: float | int | None = None
    grid_loss_remaining: float | int | None = None
    grid_loss_zero_applied: bool | None = None

    # Feature flags / derived
    booleans: dict[str, bool] | None = None
    features: dict[str, bool] | None = None
    mppt_individual: list[float | int] | None = None
    mppt_chargers: list[dict[str, Any]] | None = None
    # AC PV inverters of any vendor: [{name?, power, voltage?, current?}]
    pv_inverters: list[dict[str, Any]] | None = None
    batteries: list[dict[str, Any]] | None = None
    loads: dict[str, float | int] | None = None
    ui_config: dict[str, Any] | None = None
    daily_stats: dict[str, Any] | None = None

    # Solar forecast computed upstream by inverter-control:
    # {date, generated_at, today_kwh, tomorrow_kwh}
    solar_forecast: dict[str, Any] | None = None

    # Rich HA entity displays from ha_client (HaFilteredData shape):
    # {sensors[], numbers[], covers[], media_players[], scenes[], weather}
    ha_filtered: dict[str, Any] | None = None

    # EV
    ev_charging_kw: float | int | None = None
    ev_power: float | int | None = None
    car_soc: float | int | None = None

    # Water
    water_level: float | int | None = None
    water_valve: bool | str | None = None
    pump_switch: bool | str | None = None
    pump_mode: float | int | None = None
    water_valve_mode: float | int | None = None

    # Appliances
    dishwasher_running: bool | None = None
    dishwasher_duration: float | int | None = None
    washer_time: float | int | None = None
    washer_power: float | int | bool | None = None
    dryer_time: float | int | None = None
    dryer_power: float | int | bool | None = None

    # Notifications (inverter-control pushes + Victron alarm transitions)
    notifications: list[dict[str, Any]] | None = None

    # Latest camera event (Frigate): {camera, url, ts}
    camera_event: dict[str, Any] | None = None


# Mutable module-level state (avoids global statements)
_state: dict[str, Any] = {"latest_version": None, "mqtt_state": None, "app_state": None}


def set_latest_version(version: str | None) -> None:
    """Update cached latest version"""
    _state["latest_version"] = version


def set_mqtt_state(mqtt_state):
    """Set the MqttState reference for state reads."""
    _state["mqtt_state"] = mqtt_state


def set_app_state(app_state) -> None:
    """Share current transport status without keeping a stale MQTT client."""
    _state["app_state"] = app_state


def _native_water_context():
    app_state = _state.get("app_state")
    if gateway.prefer_gateway() or getattr(app_state, "data_source", None) != "mqtt":
        raise RuntimeError(
            "Water mode control requires direct Cerbo MQTT; gateway control is unsupported"
        )
    if not getattr(app_state, "mqtt_connected", False) or app_state.mqtt_client is None:
        raise RuntimeError("Direct Cerbo MQTT is not connected")
    mqtt_state = app_state.mqtt_state
    portal = getattr(mqtt_state, "_portal_id", "")
    if (
        not isinstance(portal, str)
        or not portal
        or any(c in "/+#\0" or c.isspace() for c in portal)
    ):
        raise RuntimeError("A valid Cerbo portal is required for water mode control")
    return app_state.mqtt_client, mqtt_state, portal


def _can_control_water() -> bool:
    try:
        _native_water_context()
    except RuntimeError:
        return False
    return True


async def _set_water_mode(data: dict[str, Any], mqtt_client: Client | None) -> None:
    which, mode = data.get("which"), data.get("mode")
    if which not in ("pump", "valve") or type(mode) not in (int, float) or mode not in (0, 1, 2):
        raise ValueError("Water mode requires pump or valve and integer mode 0, 1 or 2")
    current_client, mqtt_state, portal = _native_water_context()
    if mqtt_client is not current_client:
        raise RuntimeError("The direct MQTT connection changed; retry the water action")
    instance = config.WATER_PUMP_INSTANCE if which == "pump" else config.WATER_VALVE_INSTANCE
    if isinstance(instance, bool) or not isinstance(instance, int) or instance < 0:
        raise RuntimeError("Water device instance is not configured")
    leaves = dict(mqtt_state._devices("pump")).get(str(instance), {})
    if number(leaves.get("Mode")) not in (0, 1, 2):
        raise RuntimeError("The configured water device has no available native Mode")
    await current_client.publish(
        f"W/{portal}/pump/{instance}/Mode", json.dumps({"value": int(mode)}), qos=1, retain=False
    )


def build_payload() -> dict[str, Any]:
    """Build the canonical state payload sent to all WebSocket clients."""
    mqtt = _state["mqtt_state"]
    raw_state = ha_client.merge_overlay(mqtt.get_state())

    # Use Pydantic model to filter/validate - extra="ignore" drops unknown keys
    validated = InverterState(**raw_state)

    # Preserve explicit nulls so polling clients can clear invalidated values.
    filtered = validated.model_dump(exclude_unset=True)
    app_state = _state.get("app_state")
    transport = {
        key: getattr(app_state, key)
        for key in ("data_source", "mqtt_connected", "gateway_connected")
        if hasattr(app_state, key)
    }

    return _with_ui_config(
        {
            **filtered,
            **transport,
            "notifications": mqtt.get_notifications(),
            "camera_event": mqtt.camera_event,
            "dashboard_version": VERSION,
            "latest_version": _state["latest_version"],
            "water_controls_available": _can_control_water(),
        }
    )


# Runtime-editable UI settings (dashboard_settings.json via /api/settings)
_ui_settings: dict[str, Any] = {}


def set_ui_settings(settings: dict[str, Any]) -> None:
    """Hot-apply saved settings to the broadcast pipeline."""
    global _ui_settings
    _ui_settings = dict(settings)


def get_ui_settings() -> dict[str, Any]:
    """Currently applied settings (defaults + persisted overrides)."""
    return dict(_ui_settings)


def _with_ui_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge local_config-derived ui_config (e.g. home_buttons) into payload."""
    patch = ha_client.ui_config_patch()
    out = dict(payload)
    uc = dict(out.get("ui_config") or {})
    if _ui_settings:
        uc["settings"] = {k: v for k, v in _ui_settings.items() if k.startswith("show_")}
    uc.update(patch)
    if uc:
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


# Inverter-control flags published on Cerbo MQTT inverter/state.booleans
# (same set as inverter-desktop). Always toggle via MQTT with the bare key —
# never via HA input_boolean / binary_sensor mirrors.
_CONTROL_FLAG_KEYS = frozenset(
    {
        "only_charging",
        "no_feed",
        "house_support",
        "charge_battery",
        "do_not_supply_charger",
        "set_limit_to_ev_charger",
        "minimize_charging",
    }
)


def _control_flag_key(entity: str | None) -> str | None:
    if not entity or not isinstance(entity, str):
        return None
    raw = entity.strip()
    if not raw:
        return None
    key = raw.split(".")[-1] if "." in raw else raw
    return key if key in _CONTROL_FLAG_KEYS else None


async def _acknowledge_victron_on_cerbo(app_state, mqtt_client: Client | None) -> None:
    """Mirror desktop acknowledge_victron_banner: IGW command or LAN MQTT AcknowledgeAll."""
    from . import config

    if gateway.prefer_gateway():
        try:
            await gateway.post_command("acknowledge_all_notifications", {})
            logger.info("Acknowledged Victron notifications via IGW")
        except Exception:
            logger.exception("IGW acknowledge_all_notifications failed")
        return

    portal = ""
    ms = _state.get("mqtt_state")
    if ms is not None:
        portal = getattr(ms, "_portal_id", "") or ""
    portal = portal or (config.CERBO_PORTAL_ID or "")
    if not portal or mqtt_client is None:
        logger.warning("Cannot acknowledge Victron notifications: no portal/MQTT")
        return
    topic = f"W/{portal}/platform/0/Notifications/AcknowledgeAll"
    try:
        await mqtt_client.publish(topic, '{"value":1}', qos=0)
        logger.info("Published Cerbo AcknowledgeAll on %s", topic)
    except Exception:
        logger.exception("MQTT AcknowledgeAll publish failed")


async def _dispatch_action(action: str, data: dict[str, Any], mqtt_client: Client):
    """Dispatch a single WebSocket action."""
    if action == "water_mode":
        await _set_water_mode(data, mqtt_client)
    elif action in ("number_set", "set_cover_position", "media_player", "scene_activate"):
        if not await ha_client.perform_action(action, data.get("entity"), data):
            raise RuntimeError("Home Assistant action failed")
        fresh = await ha_client.fetch_states_once()
        if fresh.get("ha_direct_connected"):
            ha_client.replace_overlay(fresh)
        await broadcast_state()
    elif action == "toggle":
        entity = data.get("entity")
        flag = _control_flag_key(entity if isinstance(entity, str) else None)
        if flag:
            # Mirror desktop: Cerbo MQTT inverter/cmd/toggle with bare flag key.
            payload = {"entity": flag}
            if "state" in data:
                payload["state"] = data["state"]
            await mqtt_publish(mqtt_client, "toggle", payload)
            return
        if entity and ha_client.is_direct_mode() and ha_client.is_toggle_allowed(entity):
            if ha_client.domain_for_press(entity):
                succeeded = await ha_client.press_entity(entity)
            else:
                succeeded = await ha_client.toggle_entity(entity)
            if not succeeded:
                raise RuntimeError("Home Assistant action failed")
            fresh = await ha_client.fetch_states_once()
            if fresh.get("ha_direct_connected"):
                ha_client.replace_overlay(fresh)
            await broadcast_state()
            return
        await mqtt_publish(mqtt_client, "toggle", {"entity": entity})
    elif action == "press":
        entity = data.get("entity")
        if ha_client.is_direct_mode():
            if (
                not isinstance(entity, str)
                or not ha_client.is_toggle_allowed(entity)
                or ha_client.domain_for_press(entity) is None
            ):
                raise ValueError("Button is not configured for direct Home Assistant control")
            if not await ha_client.press_entity(entity):
                raise RuntimeError("Home Assistant button press failed")
            fresh = await ha_client.fetch_states_once()
            if fresh.get("ha_direct_connected"):
                ha_client.replace_overlay(fresh)
            await broadcast_state()
            return
        await mqtt_publish(mqtt_client, "press", {"entity": entity})
    elif action == "setpoint":
        await mqtt_publish(mqtt_client, "setpoint", {"value": data.get("value")})
    elif action == "dry_run":
        await mqtt_publish(mqtt_client, "dry_run", {})
    elif action == "limits":
        await mqtt_publish(
            mqtt_client,
            "limits",
            {
                "min": data.get("min", DEFAULT_POWER_MIN),
                "max": data.get("max", DEFAULT_POWER_MAX),
            },
        )
    elif action == "ess_mode":
        await mqtt_publish(mqtt_client, "ess_mode", {})
    elif action == "loop_interval":
        await mqtt_publish(
            mqtt_client,
            "loop_interval",
            {"interval": data.get("interval", DEFAULT_LOOP_INTERVAL)},
        )
    elif action == "set_settings":
        try:
            saved = settings_store.save_settings(data)
        except ValueError:
            logger.warning("Rejected invalid settings patch: %s", list(data))
            return
        set_ui_settings(saved)
        await broadcast_state()
    elif action == "dismiss_notification":
        # Wired from Vue NotificationBanner X — same UX as inverter-desktop.
        nid = data.get("id")
        ms = _state.get("mqtt_state")
        if not isinstance(nid, str) or not nid or ms is None:
            return
        ms.dismiss_notification(nid)
        if nid.startswith("victron-platform-"):
            await _acknowledge_victron_on_cerbo(None, mqtt_client)
        elif nid.startswith("victron-") and gateway.prefer_gateway():
            # Raw Alarms/* fallback — best-effort silence via IGW whitelist.
            try:
                await gateway.post_command("silence_alarm", {})
            except Exception:
                logger.exception("IGW silence_alarm failed")
        await broadcast_state()


async def handle_websocket(websocket: WebSocket, app_state):
    """Handle WebSocket connection.

    ``app_state`` is the server's AppState container; the MQTT client is read
    fresh for each action so commands survive reconnects (the aiomqtt client
    object is replaced on every reconnection).
    """
    await websocket.accept()
    ws_clients.add(websocket)
    logger.info("WebSocket client connected (%d total)", len(ws_clients))

    try:
        await websocket.send_json(build_payload())

        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            if action:
                await _dispatch_action(action, data, app_state.mqtt_client)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error")
    finally:
        ws_clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(ws_clients))
