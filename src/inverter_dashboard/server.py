#!/usr/bin/env python3
"""
Remote Web Dashboard for Inverter Control
Connects to Cerbo GX via MQTT, serves dashboard via WebSocket
"""

import asyncio
import json
import logging
import argparse
import os
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Callable, Any

from fastapi import FastAPI, WebSocket, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from aiomqtt import Client, MqttError

from . import config
from .config import MQTT_HOST, MQTT_PORT, WEB_PORT, DASHBOARD_SECRET
from .version import VERSION, check_latest_version, download_and_update, SelfUpdateDisabled
from . import websocket_handler
from . import ha_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class MqttState:
    """Encapsulated MQTT state."""

    def __init__(self) -> None:
        self.current_state: dict[str, Any] = {}
        self.console_lines: list[str] = []
        self._acload_names: dict[str, str] = {}
        self._acload_powers: dict[str, float] = {}
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

            elif "/acload/" in topic:
                self._handle_acload(topic, payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.exception("MQTT message parse error: %s", e)
        except Exception as e:
            logger.exception("MQTT message error: %s", e)

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

    def get_state(self) -> dict[str, Any]:
        """Get current state"""
        return self.current_state

    def get_console(self) -> list[str]:
        """Get console lines"""
        return self.console_lines


@dataclass
class AppState:
    """Application state container."""
    mqtt_state: MqttState | None = None
    mqtt_client: Client | None = None
    mqtt_tasks: list[asyncio.Task] = None

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


def _start_mqtt_client():
    """Start MQTT client connection and message loop."""
    _app_state.mqtt_state = MqttState()
    _app_state.mqtt_client = Client(
        hostname=config.MQTT_HOST,
        port=config.MQTT_PORT,
        username=config.MQTT_USERNAME,
        password=config.MQTT_PASSWORD,
        tls_params=None,
        tls_insecure=False,
    )
    _app_state.mqtt_state.set_state_callback(websocket_handler.broadcast_state)
    websocket_handler.set_mqtt_state(_app_state.mqtt_state)

    async def mqtt_connect_and_loop():
        await _app_state.mqtt_client.__aenter__()  # pylint: disable=unnecessary-dunder-call
        logger.info("Connected to MQTT broker")
        await _app_state.mqtt_client.subscribe("inverter/state")
        await _app_state.mqtt_client.subscribe("inverter/console")
        try:
            await _app_state.mqtt_client.subscribe("N/+/acload/+/Ac/Power")
            await _app_state.mqtt_client.subscribe("N/+/acload/+/CustomName")
        except Exception as e:
            logger.warning("Could not subscribe to N/+/acload topics: %s", e)
        logger.info("Subscribed to MQTT topics")

        try:
            async for message in _app_state.mqtt_client.messages:
                await _app_state.mqtt_state.on_message(message.topic.value, message.payload)
        except MqttError:
            logger.exception("MQTT message loop error")
        except Exception as e:  # pylint: disable=broad-except
            if isinstance(e, asyncio.CancelledError):
                raise
            logger.exception("Unexpected error in message loop: %s", e)

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
    """Close MQTT client connection."""
    if _app_state.mqtt_client:
        try:
            await _app_state.mqtt_client.__aexit__(None, None, None)  # pylint: disable=unnecessary-dunder-call
        except asyncio.CancelledError:
            # Cleanup already done in finally, re-raise to propagate cancellation
            _app_state.mqtt_client = None
            raise
        except Exception as e:  # pylint: disable=broad-except
            logger.exception("Error closing MQTT client: %s", e)
        finally:
            if _app_state.mqtt_client:
                _app_state.mqtt_client = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan handler"""
    # Startup
    ha_client.load_config()
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
async def index():
    """Serve Vue SPA from static/dist or 404 if not built"""
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
    await websocket_handler.handle_websocket(websocket, _app_state.mqtt_client)


@app.get("/api/state")
async def api_state():
    """Minimal JSON for Docker HEALTHCHECK and monitoring (not full inverter payload)."""
    raw = _app_state.mqtt_state.get_state()
    return {
        "ok": True,
        "dashboard_version": VERSION,
        "control_version": raw.get("version"),
        "has_mqtt_state": bool(raw),
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
    parser.add_argument("--mqtt-host", default=MQTT_HOST, help="MQTT broker host")
    parser.add_argument("--mqtt-port", type=int, default=MQTT_PORT, help="MQTT broker port")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="Web server port")
    parser.add_argument("--ssl-cert", help="SSL certificate file")
    parser.add_argument("--ssl-key", help="SSL key file")
    args = parser.parse_args()

    # Update config
    config.MQTT_HOST = args.mqtt_host
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
