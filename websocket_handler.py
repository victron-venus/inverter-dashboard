"""
WebSocket handler for real-time dashboard updates
"""

import os
import sys

_WH_DIR = os.path.dirname(os.path.abspath(__file__))
if _WH_DIR not in sys.path:
    sys.path.insert(0, _WH_DIR)

import json
import logging
from typing import Set, Dict, Any

from fastapi import WebSocket, WebSocketDisconnect

import mqtt_handler
import ha_client
from version import VERSION

logger = logging.getLogger(__name__)

# Connected WebSocket clients
ws_clients: Set[WebSocket] = set()

# Latest version (cached)
latest_version: str | None = None


def set_latest_version(version: str | None):
    """Update cached latest version"""
    global latest_version
    latest_version = version


async def broadcast_state():
    """Send state to all WebSocket clients"""
    if not ws_clients:
        return
    
    state = ha_client.merge_overlay(mqtt_handler.get_state())
    data = {
        **state,
        'console': mqtt_handler.get_console()[-20:],
        'dashboard_version': VERSION,
        'latest_version': latest_version,
    }
    
    message = json.dumps(data)
    disconnected = set()
    
    for ws in ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    
    # Remove disconnected clients
    for ws in disconnected:
        ws_clients.discard(ws)


async def handle_websocket(websocket: WebSocket, mqtt_client):
    """Handle WebSocket connection"""
    await websocket.accept()
    ws_clients.add(websocket)
    logger.info(f"WebSocket client connected ({len(ws_clients)} total)")
    
    try:
        # Send initial state
        state = ha_client.merge_overlay(mqtt_handler.get_state())
        await websocket.send_json({
            **state,
            'console': mqtt_handler.get_console()[-20:],
            'dashboard_version': VERSION,
            'latest_version': latest_version,
        })
        
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
                    'min': data.get('min', -2300),
                    'max': data.get('max', 2250)
                })
            elif action == 'ess_mode':
                mqtt_handler.publish_command(mqtt_client, 'ess_mode', {})
            elif action == 'loop_interval':
                mqtt_handler.publish_command(mqtt_client, 'loop_interval', {
                    'interval': data.get('interval', 0.33)
                })
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        ws_clients.discard(websocket)
        logger.info(f"WebSocket client disconnected ({len(ws_clients)} remaining)")
