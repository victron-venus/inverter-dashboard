#!/usr/bin/env python3
"""
Remote Web Dashboard for Inverter Control
Connects to Cerbo GX via MQTT, serves dashboard via WebSocket
"""

import os
import asyncio
import logging
import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from . import config
from .config import MQTT_HOST, MQTT_PORT, WEB_PORT, DASHBOARD_SECRET
from .version import VERSION, check_latest_version, download_and_update, SelfUpdateDisabled
from . import mqtt_handler
from . import websocket_handler
from . import ha_client
from .html_template import get_dashboard_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Mutable module-level state (avoids global statements)
_mqtt: dict = {"client": None, "state": None}


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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan handler"""

    # Startup
    ha_client.load_config()
    _mqtt["state"] = mqtt_handler.MqttState()
    _mqtt["client"] = mqtt_handler.create_client(_mqtt["state"])
    _mqtt["state"].set_state_callback(websocket_handler.broadcast_state, asyncio.get_running_loop())
    websocket_handler.set_mqtt_state(_mqtt["state"])
    mqtt_handler.start_client(_mqtt["client"])
    ha_task = None
    if ha_client.is_direct_mode():
        ha_task = asyncio.create_task(ha_client.ha_poll_loop())

    # Check for updates in background (non-blocking)
    async def _bg_version_check():
        latest = await check_latest_version()
        if latest:
            websocket_handler.set_latest_version(latest)

    _bg_task = asyncio.create_task(_bg_version_check())

    yield

    # Shutdown
    if ha_task:
        ha_task.cancel()
        try:
            await ha_task
        except asyncio.CancelledError:
            pass
    mqtt_handler.stop_client(_mqtt["client"])


app = FastAPI(title="Inverter Dashboard", lifespan=lifespan)


# Routes
@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve dashboard page"""
    return get_dashboard_html()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    token = websocket.query_params.get("token")
    if DASHBOARD_SECRET and token != DASHBOARD_SECRET:
        await websocket.close(code=4401, reason="unauthorized")
        return
    await websocket_handler.handle_websocket(websocket, _mqtt["client"])


@app.get("/api/state")
async def api_state():
    """Minimal JSON for Docker HEALTHCHECK and monitoring (not full inverter payload)."""
    raw = _mqtt["state"].get_state()
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
        success, result = await download_and_update()
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
    logger.info("  Web:  %s://0.0.0.0:%s", proto, args.port)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port,
        ssl_certfile=args.ssl_cert,
        ssl_keyfile=args.ssl_key,
        log_level="info",
    )


if __name__ == "__main__":
    main()
