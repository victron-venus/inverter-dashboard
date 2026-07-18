"""
WebSocket handler for real-time dashboard updates
"""

import json
import logging
from typing import Set, Dict, Any

from fastapi import WebSocket, WebSocketDisconnect

from . import mqtt_handler
from . import ha_client
from .version import VERSION
from .config import DEFAULT_POWER_MIN, DEFAULT_POWER_MAX, DEFAULT_LOOP_INTERVAL, CONSOLE_SEND_LINES

logger = logging.getLogger(__name__)

# Connected WebSocket clients
ws_clients: Set[WebSocket] = set()

# Latest version (cached)
latest_version: str | None = None


def set_latest_version(version: str | None):
    """Update cached latest version"""
    global latest_version
    latest_version = version


def build_payload() -> Dict[str, Any]:
    """Build the canonical state payload sent to all WebSocket clients."""
    state = ha_client.merge_overlay(mqtt_handler.get_state())
    return _with_ui_config({
        **state,
        'console': mqtt_handler.get_console()[-CONSOLE_SEND_LINES:],
        'dashboard_version': VERSION,
        'latest_version': latest_version,
    })


def _with_ui_config(payload: Dict[str, Any]) -> Dict[str, Any]:
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


async def handle_websocket(websocket: WebSocket, mqtt_client):
    """Handle WebSocket connection"""
    await websocket.accept()
    ws_clients.add(websocket)
    logger.info("WebSocket client connected (%d total)", len(ws_clients))

    try:
        # Send initial state
        await websocket.send_json(build_payload())

        # Handle incoming messages
        while True:
            data = await websocket.receive_json()
            action = data.get('action')

            if action == 'toggle':
                entity = data.get('entity')
                if (
                    entity
                    and ha_client.is_direct_mode()
                    and ha_client.is_toggle_allowed(entity)
                ):
                    await ha_client.toggle_entity(entity)
                    fresh = await ha_client.fetch_states_once()
                    if fresh.get("ha_direct_connected"):
                        ha_client.replace_overlay(fresh)
                    await broadcast_state()
                else:
                    mqtt_handler.publish_command(mqtt_client, 'toggle', {'entity': entity})
            elif action == 'press':
                mqtt_handler.publish_command(mqtt_client, 'press', {'entity': data.get('entity')})
            elif action == 'setpoint':
                mqtt_handler.publish_command(mqtt_client, 'setpoint', {'value': data.get('value')})
            elif action == 'dry_run':
                mqtt_handler.publish_command(mqtt_client, 'dry_run', {})
            elif action == 'limits':
                mqtt_handler.publish_command(mqtt_client, 'limits', {
                    'min': data.get('min', DEFAULT_POWER_MIN),
                    'max': data.get('max', DEFAULT_POWER_MAX)
                })
            elif action == 'ess_mode':
                mqtt_handler.publish_command(mqtt_client, 'ess_mode', {})
            elif action == 'loop_interval':
                mqtt_handler.publish_command(mqtt_client, 'loop_interval', {
                    'interval': data.get('interval', DEFAULT_LOOP_INTERVAL)
                })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
    finally:
        ws_clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(ws_clients))
