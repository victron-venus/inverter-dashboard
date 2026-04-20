#!/usr/bin/env python3
"""
Remote Web Dashboard for Inverter Control
Connects to Cerbo GX via MQTT, serves Vue.js dashboard via WebSocket
"""

import os
import sys
import asyncio
import logging
import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from config import MQTT_HOST, MQTT_PORT, WEB_PORT
from version import VERSION, check_latest_version, download_and_update
import mqtt_handler
import websocket_handler
import ha_client
from html_template import get_dashboard_html

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# MQTT client instance
mqtt_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    global mqtt_client
    
    # Startup
    ha_client.load_config()
    mqtt_client = mqtt_handler.create_client()
    mqtt_handler.set_state_callback(websocket_handler.broadcast_state, asyncio.get_event_loop())
    mqtt_handler.start_client(mqtt_client)
    ha_task = None
    if ha_client.is_direct_mode():
        ha_task = asyncio.create_task(ha_client.ha_poll_loop())
    
    # Check for updates on startup
    latest = await check_latest_version()
    if latest:
        websocket_handler.set_latest_version(latest)
    
    yield
    
    # Shutdown
    if ha_task:
        ha_task.cancel()
        try:
            await ha_task
        except asyncio.CancelledError:
            pass
    mqtt_handler.stop_client(mqtt_client)


app = FastAPI(title="Inverter Dashboard", lifespan=lifespan)


# Routes
@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve dashboard page"""
    return get_dashboard_html()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await websocket_handler.handle_websocket(websocket, mqtt_client)


@app.post("/api/check-update")
async def api_check_update():
    """Check for updates"""
    latest = await check_latest_version()
    if latest:
        websocket_handler.set_latest_version(latest)
    return {'current': VERSION, 'latest': latest}


@app.post("/api/update")
async def api_update():
    """Self-update: download latest from GitHub and restart"""
    logger.info("Update requested...")
    
    success, result = await download_and_update()
    
    if success:
        # Schedule restart
        asyncio.get_event_loop().call_later(1, lambda: os._exit(0))
        return {'status': 'updated', 'version': result, 'message': f'Updated to v{result}, restarting...'}
    else:
        return JSONResponse({'error': result}, status_code=500)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Remote Web Dashboard for Inverter Control')
    parser.add_argument('--mqtt-host', default=MQTT_HOST, help='MQTT broker host')
    parser.add_argument('--mqtt-port', type=int, default=MQTT_PORT, help='MQTT broker port')
    parser.add_argument('--port', type=int, default=WEB_PORT, help='Web server port')
    parser.add_argument('--ssl-cert', help='SSL certificate file')
    parser.add_argument('--ssl-key', help='SSL key file')
    args = parser.parse_args()
    
    # Update config
    import config
    config.MQTT_HOST = args.mqtt_host
    config.MQTT_PORT = args.mqtt_port
    
    proto = "https" if args.ssl_cert else "http"
    print(f"Starting Remote Dashboard v{VERSION}")
    print(f"  MQTT: {args.mqtt_host}:{args.mqtt_port}")
    print(f"  Web:  {proto}://0.0.0.0:{args.port}")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port,
        ssl_certfile=args.ssl_cert,
        ssl_keyfile=args.ssl_key,
        log_level="info"
    )


if __name__ == "__main__":
    main()
