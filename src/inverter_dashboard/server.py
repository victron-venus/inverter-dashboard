#!/usr/bin/env python3
"""
Remote Web Dashboard for Inverter Control
Connects to Cerbo GX via MQTT, serves dashboard via WebSocket
"""

import argparse
import asyncio
import fnmatch
import json
import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from aiomqtt import Client, MqttError, TLSParameters
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, ha_client, settings_store, websocket_handler
from .config import DASHBOARD_SECRET, WEB_PORT
from .version import VERSION, SelfUpdateDisabled, check_latest_version, download_and_update

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _capitalize(s: str) -> str:
    return s[:1].upper() + s[1:] if s else ""


def _split_camel(s: str) -> list[str]:
    words: list[str] = []
    current = ""
    for ch in s:
        if ch.isupper() and current and current[-1].islower():
            words.append(current)
            current = ch
        elif ch in ("_", "-", " "):
            if current:
                words.append(current)
            current = ""
        else:
            current += ch
    if current:
        words.append(current)
    return words


def pretty_alarm_name(name: str) -> str:
    """'HighCellVoltage' / 'high_cell_voltage' -> 'High Cell Voltage'"""
    return " ".join(_capitalize(w) for w in _split_camel(name))


def pretty_service_name(service: str) -> str:
    """'battery_512' -> 'Battery 512', 'vebus' -> 'Vebus'"""
    name, _, inst = service.rpartition("_")
    if name and inst.isdigit():
        return f"{_capitalize(name)} {inst}"
    return _capitalize(service)


class MqttState:
    """Encapsulated MQTT state."""

    NOTIFICATIONS_MAX = 100

    def __init__(self) -> None:
        self.current_state: dict[str, Any] = {}
        self.console_lines: list[str] = []
        self.notifications: list[dict[str, Any]] = []
        self._acload_names: dict[str, str] = {}
        self._acload_powers: dict[str, float] = {}
        # Discovered PV inverters keyed by GX instance: {power, voltage, current, name}
        self._pv_inverters: dict[str, dict[str, Any]] = {}
        self._alarm_values: dict[str, int] = {}
        self.camera_event: dict[str, Any] | None = None
        self._on_state_update: Callable | None = None

    def set_state_callback(self, callback: Callable) -> None:
        """Set callback to be called when state updates"""
        self._on_state_update = callback

    async def on_message(self, topic: str, payload: bytes) -> None:
        """Process incoming MQTT message"""
        try:
            if topic == "inverter/state":
                self.current_state = json.loads(payload.decode())
                if self._on_state_update:
                    await self._on_state_update()

            elif topic == "inverter/console":
                line = payload.decode()
                self.console_lines.append(line)
                if len(self.console_lines) > config.CONSOLE_MAX_LINES:
                    self.console_lines.pop(0)

            elif topic == "inverter/notifications":
                self.push_notification(json.loads(payload.decode()))
                if self._on_state_update:
                    await self._on_state_update()

            elif "/Alarms/" in topic:
                changed = self.handle_alarm(topic, payload)
                if changed and self._on_state_update:
                    await self._on_state_update()

            elif config.CAMERA_TOPIC and fnmatch.fnmatch(
                topic, config.CAMERA_TOPIC.replace("+", "*")
            ):
                self.handle_camera_event(payload)
                if self.camera_event and self._on_state_update:
                    await self._on_state_update()

            elif "/acload/" in topic:
                self._handle_acload(topic, payload)

            elif "/pvinverter/" in topic:
                if self._handle_pvinverter(topic, payload) and self._on_state_update:
                    await self._on_state_update()

            elif "/tank/" in topic or "/pump/" in topic:
                self.handle_water(topic, payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("MQTT message parse error")
        except Exception:
            logger.exception("MQTT message error")

    def push_notification(self, data: Any) -> None:
        """Append a notification from inverter-control (MqttNotification shape)."""
        if not isinstance(data, dict):
            return
        notif = {
            "id": str(data.get("id") or ""),
            "level": str(data.get("level") or "info"),
            "title": str(data.get("title") or ""),
            "body": str(data.get("body") or ""),
            "source": str(data.get("source") or "inverter-control"),
            "ts": str(data.get("ts") or ""),
        }
        self.notifications.append(notif)
        if len(self.notifications) > self.NOTIFICATIONS_MAX:
            self.notifications = self.notifications[-self.NOTIFICATIONS_MAX :]

    def handle_alarm(self, topic: str, payload: bytes) -> bool:
        """Track a Victron alarm topic (value 0/1/2); emit/clear on transition.

        Returns True when the notification list changed.
        """
        try:
            val = json.loads(payload.decode()).get("value")
        except (ValueError, AttributeError):
            return False
        value = int(val) if isinstance(val, (int, float)) else 0
        prev = self._alarm_values.get(topic, 0)
        if prev == value:
            return False
        self._alarm_values[topic] = value

        nid = f"victron-{topic}"
        if value not in (1, 2):
            # 0 = cleared: drop matching banner notifications
            before = len(self.notifications)
            self.notifications = [n for n in self.notifications if n["id"] != nid]
            return len(self.notifications) != before

        parts = topic.split("/")
        service = parts[2] if len(parts) > 4 else "device"
        alarm_name = parts[4] if len(parts) > 4 else topic
        level = "alarm" if value == 2 else "warning"
        state_txt = "Alarm" if value == 2 else "Warning"
        self.push_notification(
            {
                "id": nid,
                "level": level,
                "title": pretty_service_name(service),
                "body": f"{pretty_alarm_name(alarm_name)}: {state_txt}",
                "source": "victron",
            }
        )
        return True

    def _handle_acload(self, topic: str, payload: bytes) -> None:
        """Decode acload topic messages into power/name maps.

        Topic structure: N/<portal_id>/acload/<instance>/<path...>
        """
        parts = topic.split("/")
        if len(parts) < 5 or parts[2] != "acload":
            return
        instance = parts[3]
        path = "/".join(parts[4:])
        try:
            data = json.loads(payload.decode())
            val = data.get("value")
            if path in ("Ac/Power", "Ac/L1/Power") and isinstance(val, (int, float)):
                self._acload_powers[instance] = float(val)
                self._sync_acload_to_state()
            elif path == "CustomName" and isinstance(val, str) and val.strip():
                self._acload_names[instance] = val.strip()
                self._sync_acload_to_state()
        except (ValueError, AttributeError):
            pass

    def _sync_acload_to_state(self) -> None:
        """Merge decoded acload topics into current_state['loads'] if loads is empty or missing."""
        if not self._acload_powers:
            return
        current_loads = dict(self.current_state.get("loads") or {})
        changed = False
        for instance, power in self._acload_powers.items():
            name = self._acload_names.get(instance) or f"AC Load {instance}"
            # Standardize key format (lowercase with underscores or display name)
            key = name.lower().replace(" ", "_")
            if key not in current_loads:
                current_loads[key] = power
                changed = True
        if changed:
            self.current_state["loads"] = current_loads

    def _handle_pvinverter(self, topic: str, payload: bytes) -> bool:
        """Decode GX PV-inverter topics into state['pv_inverters'].

        Topic structure: N/<portal_id>/pvinverter/<instance>/<path...>
        (payload {"value": ...}). Works with any vendor's dbus publisher
        (dbus-tasmota-pv, dbus-esphome, ...) and without inverter-control.

        Returns True when the state changed.
        """
        parts = topic.split("/")
        if len(parts) < 5 or parts[2] != "pvinverter":
            return False
        instance = parts[3]
        path = "/".join(parts[4:])
        try:
            data = json.loads(payload.decode())
            val = data.get("value")
        except (ValueError, AttributeError):
            return False

        entry = self._pv_inverters.setdefault(instance, {})
        if path in ("Ac/Power", "Ac/L1/Power") and isinstance(val, (int, float)):
            entry["power"] = float(val)
        elif path == "Ac/L1/Voltage" and isinstance(val, (int, float)):
            entry["voltage"] = float(val)
        elif path == "Ac/L1/Current" and isinstance(val, (int, float)):
            entry["current"] = float(val)
        elif path == "ProductName" and isinstance(val, str) and val.strip():
            entry["name"] = val.strip()
        else:
            return False

        ordered = [
            self._pv_inverters[k]
            for k in sorted(self._pv_inverters, key=lambda x: x.isdigit() and int(x) or 0)
        ]
        self.current_state["pv_inverters"] = ordered
        return True

    def handle_water(self, topic: str, payload: bytes) -> None:
        """Decode dbus-pump water topics into state keys.

        Topic structure: N/<portal_id>/tank/<instance>/Level and
        N/<portal_id>/pump/<instance>/State (Venus MQTT-GUI format,
        payload {"value": ...}). Requires CERBO_PORTAL_ID to be configured.
        """
        if not config.CERBO_PORTAL_ID:
            return
        parts = topic.split("/")
        if len(parts) < 5 or parts[1] != config.CERBO_PORTAL_ID:
            return
        service_type, device, path = parts[2], parts[3], "/".join(parts[4:])
        try:
            data = json.loads(payload.decode())
            val = data.get("value")
        except (ValueError, AttributeError):
            return

        # Venus bridges pump.startstop services as N/<portal>/pump/<instance>/State
        if service_type == "tank" and device == str(config.WATER_TANK_INSTANCE):
            if path == "Level" and isinstance(val, (int, float)):
                self.current_state["water_level"] = float(val)
        elif service_type == "pump" and path == "State" and isinstance(val, (int, float)):
            if device == str(config.WATER_VALVE_INSTANCE):
                self.current_state["water_valve"] = bool(val)
            elif device == str(config.WATER_PUMP_INSTANCE):
                self.current_state["pump_switch"] = bool(val)

    def get_state(self) -> dict[str, Any]:
        """Get current state"""
        return self.current_state

    def get_console(self) -> list[str]:
        """Get console lines"""
        return self.console_lines

    def get_notifications(self) -> list[dict[str, Any]]:
        """Get notification list (inverter-control pushes + alarm transitions)."""
        return self.notifications

    def handle_camera_event(self, payload: bytes) -> None:
        """Store the latest camera event (desktop CameraEvent shape: {agent_name, video_url, timestamp})."""
        try:
            data = json.loads(payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = payload.decode(errors="replace")
        if isinstance(data, dict):
            self.camera_event = {
                "camera": str(data.get("agent_name") or "Camera"),
                "url": str(data.get("video_url") or ""),
                "ts": str(data.get("timestamp") or ""),
            }
        else:
            # Raw string payload treated as a direct stream/snapshot URL
            self.camera_event = {"camera": "Camera", "url": str(data), "ts": ""}


@dataclass
class AppState:
    """Application state container."""

    mqtt_state: MqttState | None = None
    mqtt_client: Client | None = None
    mqtt_tasks: list[asyncio.Task] = None
    mqtt_connected: bool = False
    mqtt_reconnects: int = 0

    def __post_init__(self):
        if self.mqtt_tasks is None:
            self.mqtt_tasks = []


# Module-level app state
_app_state = AppState()


def _verify_secret(request: Request, token: str | None = None) -> None:
    """Verify DASHBOARD_SECRET against Authorization header or query param.

    Raises HTTPException(401/403) on failure.
    """
    if not DASHBOARD_SECRET:
        return

    if token and token == DASHBOARD_SECRET:
        return

    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and auth[7:] == DASHBOARD_SECRET:
        return

    if not token and not auth:
        raise HTTPException(status_code=401, detail="missing secret")

    raise HTTPException(status_code=403, detail="invalid secret")


def _make_mqtt_client() -> Client:
    """Create a fresh MQTT client from config (a closed client cannot be reused)."""
    # NB: tls_insecure must not be passed without an SSL context - paho raises
    # ValueError and the message-loop task would die silently at startup.
    client_kwargs: dict[str, Any] = {
        "hostname": config.MQTT_HOST,
        "port": config.MQTT_PORT,
        # Random suffix keeps the client ID unique so a second instance or a
        # stale broker session cannot kick this client off the broker.
        "identifier": f"inverter-dashboard-{os.urandom(3).hex()}",
        "username": config.MQTT_USERNAME,
        "password": config.MQTT_PASSWORD,
    }
    if config.MQTT_TLS:
        client_kwargs["tls_params"] = TLSParameters(ca_certs=config.MQTT_CA_CERT or None)
    return Client(**client_kwargs)


async def _subscribe_topics(client: Client) -> None:
    """Subscribe to all dashboard topics after connecting."""
    await client.subscribe("inverter/state")
    await client.subscribe("inverter/console")
    if config.CAMERA_TOPIC:
        try:
            await client.subscribe(config.CAMERA_TOPIC)
        except Exception as e:
            logger.warning("Could not subscribe to %s: %s", config.CAMERA_TOPIC, e)
    try:
        await client.subscribe("inverter/notifications")
    except Exception as e:
        logger.warning("Could not subscribe to inverter/notifications: %s", e)
    if config.CERBO_PORTAL_ID:
        try:
            await client.subscribe(f"N/{config.CERBO_PORTAL_ID}/+/Alarms/#")
        except Exception as e:
            logger.warning("Could not subscribe to Victron alarm topics: %s", e)
    try:
        await client.subscribe("N/+/acload/+/Ac/Power")
        await client.subscribe("N/+/acload/+/CustomName")
    except Exception as e:
        logger.warning("Could not subscribe to N/+/acload topics: %s", e)
    # AC PV inverters of any vendor (Tasmota, ESPHome, ...) published on the
    # GX broker — tiles stay alive even when inverter-control is down.
    try:
        await client.subscribe("N/+/pvinverter/+/#")
    except Exception as e:
        logger.warning("Could not subscribe to N/+/pvinverter topics: %s", e)
    if config.CERBO_PORTAL_ID:
        try:
            portal = config.CERBO_PORTAL_ID
            await client.subscribe(f"N/{portal}/tank/+/Level")
            await client.subscribe(f"N/{portal}/pump/+/State")
        except Exception as e:
            logger.warning("Could not subscribe to water topics: %s", e)


def _next_backoff(delay: float) -> float:
    """Double the reconnect delay, capped at MQTT_RECONNECT_MAX."""
    return min(delay * 2, config.MQTT_RECONNECT_MAX)


def _start_mqtt_client():
    """Start MQTT client connection and message loop with auto-reconnect."""
    _app_state.mqtt_state = MqttState()
    _app_state.mqtt_client = _make_mqtt_client()
    _app_state.mqtt_state.set_state_callback(websocket_handler.broadcast_state)
    websocket_handler.set_mqtt_state(_app_state.mqtt_state)

    async def mqtt_connect_and_loop():
        delay = max(config.MQTT_RECONNECT_MIN, 0.1)
        while True:
            try:
                async with _app_state.mqtt_client:
                    _app_state.mqtt_connected = True
                    logger.info("Connected to MQTT broker")
                    await _subscribe_topics(_app_state.mqtt_client)
                    logger.info("Subscribed to MQTT topics")
                    delay = max(config.MQTT_RECONNECT_MIN, 0.1)
                    async for message in _app_state.mqtt_client.messages:
                        await _app_state.mqtt_state.on_message(message.topic.value, message.payload)
            except asyncio.CancelledError:
                raise
            except MqttError:
                _app_state.mqtt_reconnects += 1
                logger.warning(
                    "MQTT connection lost — reconnecting in %.1fs (reconnect #%d)",
                    delay,
                    _app_state.mqtt_reconnects,
                )
            except Exception:  # pylint: disable=broad-except
                _app_state.mqtt_reconnects += 1
                logger.exception("Unexpected error in MQTT loop — retrying in %.1fs", delay)
            finally:
                _app_state.mqtt_connected = False
            await asyncio.sleep(delay)
            delay = _next_backoff(delay)
            _app_state.mqtt_client = _make_mqtt_client()

    mqtt_task = asyncio.create_task(mqtt_connect_and_loop())
    _app_state.mqtt_tasks.append(mqtt_task)


def _start_ha_polling():
    """Start HA polling task if direct mode enabled."""
    if ha_client.is_direct_mode():
        return asyncio.create_task(ha_client.ha_poll_loop())
    return None


def _start_version_check():
    """Start background version check task."""

    async def _bg_version_check():
        latest = await check_latest_version()
        if latest:
            websocket_handler.set_latest_version(latest)

    return asyncio.create_task(_bg_version_check())


async def _shutdown_tasks(ha_task):
    """Cancel and await all background tasks."""
    cancelled = False
    if ha_task:
        ha_task.cancel()
        try:
            await ha_task
        except asyncio.CancelledError:
            cancelled = True

    for task in _app_state.mqtt_tasks:
        task.cancel()
    if _app_state.mqtt_tasks:
        await asyncio.gather(*_app_state.mqtt_tasks, return_exceptions=True)

    if cancelled:
        raise asyncio.CancelledError


async def _shutdown_mqtt_client():
    """Clear MQTT client reference.

    The connection itself is closed by the message-loop task's ``async with``
    block when the task is cancelled (see ``_shutdown_tasks``).
    """
    _app_state.mqtt_connected = False
    _app_state.mqtt_client = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan handler"""
    # Startup
    ha_client.load_config()
    settings_store.apply_connection_overrides()  # file wins over env; CLI applied later wins over file
    websocket_handler.set_ui_settings(settings_store.load_settings())
    _start_mqtt_client()
    ha_task = _start_ha_polling()
    _start_version_check()

    yield

    # Shutdown
    await _shutdown_tasks(ha_task)
    await _shutdown_mqtt_client()


app = FastAPI(title="Inverter Dashboard", lifespan=lifespan)


# Mount Vue SPA dist assets if available (higher priority than fallback routes)
def _mount_vue_dist():
    """Mount Vue SPA dist/ directory if it exists."""
    static_dir = Path(__file__).parent / "static"
    dist_dir = static_dir / "dist"
    if dist_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(dist_dir)), name="vue_dist")
        logger.info("Mounted Vue SPA from %s", dist_dir)


_mount_vue_dist()


# Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, token: str | None = None):
    """Serve Vue SPA from static/dist or 404 if not built"""
    try:
        _verify_secret(request, token)
    except HTTPException as exc:
        return HTMLResponse(
            "<h1>Inverter Dashboard</h1>"
            f"<p>{exc.detail}. Append <code>?token=YOUR_SECRET</code> to the URL or send "
            "an <code>Authorization: Bearer</code> header.</p>",
            status_code=exc.status_code,
        )
    static_dir = Path(__file__).parent / "static"
    index_path = static_dir / "dist" / "index.html"
    if index_path.is_file():
        return index_path.read_text()
    return HTMLResponse(
        "<h1>Inverter Dashboard</h1><p>Vue SPA not built. Run <code>npm run build</code> in inverter-dashboard-vue and copy dist/ to static/.</p>",
        status_code=404,
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    token = websocket.query_params.get("token")
    if DASHBOARD_SECRET and token != DASHBOARD_SECRET:
        await websocket.close(code=4401, reason="unauthorized")
        return
    await websocket_handler.handle_websocket(websocket, _app_state)


@app.get(
    "/api/settings",
    responses={401: {"description": "Missing secret"}, 403: {"description": "Invalid secret"}},
)
async def api_settings_get(request: Request):
    """Current dashboard settings (section visibility, camera topic)."""
    _verify_secret(request)
    return {"ok": True, "settings": settings_store.load_settings(mask_secrets=True)}


@app.post(
    "/api/settings",
    responses={
        400: {"description": "Invalid settings"},
        401: {"description": "Missing secret"},
        403: {"description": "Invalid secret"},
    },
)
async def api_settings_post(request: Request):
    """Persist settings; section-visibility keys apply on next broadcast."""
    _verify_secret(request)
    try:
        patch = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON") from None
    if not isinstance(patch, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    try:
        saved = settings_store.save_settings(patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    websocket_handler.set_ui_settings(saved)
    return {"ok": True, "settings": saved}


@app.get("/api/state")
async def api_state():
    """Minimal JSON for Docker HEALTHCHECK and monitoring (not full inverter payload)."""
    raw = _app_state.mqtt_state.get_state() if _app_state.mqtt_state else {}
    return {
        "ok": True,
        "dashboard_version": VERSION,
        "control_version": raw.get("version"),
        "has_mqtt_state": bool(raw),
        "mqtt_connected": _app_state.mqtt_connected,
        "mqtt_reconnects": _app_state.mqtt_reconnects,
    }


@app.post(
    "/api/check-update",
    responses={401: {"description": "Missing secret"}, 403: {"description": "Invalid secret"}},
)
async def api_check_update(request: Request):
    """Check for updates"""
    _verify_secret(request)
    latest = await check_latest_version()
    if latest:
        websocket_handler.set_latest_version(latest)
    return {"current": VERSION, "latest": latest}


@app.post(
    "/api/update",
    responses={401: {"description": "Missing secret"}, 403: {"description": "Invalid secret"}},
)
async def api_update(request: Request):
    """Self-update: download latest from GitHub and restart"""
    _verify_secret(request)
    logger.info("Update requested...")

    try:
        success, result = download_and_update()
    except SelfUpdateDisabled:
        return JSONResponse(
            {"error": "self-update is disabled (set SELF_UPDATE_ENABLED=true)"}, status_code=403
        )

    if success:
        # Schedule restart via container supervisor (PID 1 reaps this process)
        asyncio.get_running_loop().call_later(1, lambda: os._exit(0))
        return {
            "status": "updated",
            "version": result,
            "message": f"Updated to v{result}, restarting...",
        }
    return JSONResponse({"error": result}, status_code=500)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Remote Web Dashboard for Inverter Control")
    parser.add_argument("--mqtt-host", default=None, help="MQTT broker host")
    parser.add_argument("--mqtt-port", type=int, default=None, help="MQTT broker port")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="Web server port")
    parser.add_argument("--ssl-cert", help="SSL certificate file")
    parser.add_argument("--ssl-key", help="SSL key file")
    args = parser.parse_args()

    # Update config: settings-file overrides already applied via lifespan;
    # explicit CLI flags win over both.
    if args.mqtt_host is not None:
        config.MQTT_HOST = args.mqtt_host
    if args.mqtt_port is not None:
        config.MQTT_PORT = args.mqtt_port

    proto = "https" if args.ssl_cert else "http"
    if not DASHBOARD_SECRET:
        logger.warning(
            "DASHBOARD_SECRET is not set — API endpoints are unprotected. "
            "Set DASHBOARD_SECRET env var for production use."
        )
    logger.info("Starting Remote Dashboard v%s", VERSION)
    logger.info("  MQTT: %s:%s", args.mqtt_host, args.mqtt_port)
    logger.info("  Web:  %s://%s:%s", proto, config.HOST, args.port)

    uvicorn.run(
        app,
        host=config.HOST,
        port=args.port,
        ssl_certfile=args.ssl_cert,
        ssl_keyfile=args.ssl_key,
        log_level="info",
    )


if __name__ == "__main__":
    main()
