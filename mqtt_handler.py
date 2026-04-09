"""
MQTT client handler for receiving state from Cerbo
"""

import json
import asyncio
import logging
from typing import Dict, Any, Callable

import paho.mqtt.client as mqtt

import config

logger = logging.getLogger(__name__)

# State storage
current_state: Dict[str, Any] = {}
console_lines: list = []

# Callback for state updates
_on_state_update: Callable | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def set_state_callback(callback: Callable, loop: asyncio.AbstractEventLoop):
    """Set callback to be called when state updates"""
    global _on_state_update, _main_loop
    _on_state_update = callback
    _main_loop = loop


def on_connect(client, userdata, flags, rc, properties=None):
    """MQTT connected - subscribe to topics"""
    logger.info(f"MQTT connected to {config.MQTT_HOST}:{config.MQTT_PORT}")
    client.subscribe("inverter/state")
    client.subscribe("inverter/console")


def on_message(client, userdata, msg):
    """MQTT message received"""
    global current_state, console_lines
    
    try:
        if msg.topic == "inverter/state":
            current_state = json.loads(msg.payload.decode())
            # Trigger callback
            if _on_state_update and _main_loop and _main_loop.is_running():
                asyncio.run_coroutine_threadsafe(_on_state_update(), _main_loop)
        
        elif msg.topic == "inverter/console":
            line = msg.payload.decode()
            console_lines.append(line)
            if len(console_lines) > 50:
                console_lines.pop(0)
    except Exception as e:
        logger.error(f"MQTT message error: {e}")


def create_client() -> mqtt.Client:
    """Create and configure MQTT client"""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    return client


def start_client(client: mqtt.Client):
    """Start MQTT client connection"""
    try:
        client.connect(config.MQTT_HOST, config.MQTT_PORT, 60)
        client.loop_start()
        logger.info(f"MQTT client started, connecting to {config.MQTT_HOST}:{config.MQTT_PORT}")
    except Exception as e:
        logger.error(f"MQTT connection failed: {e}")


def stop_client(client: mqtt.Client):
    """Stop MQTT client"""
    if client:
        client.loop_stop()
        client.disconnect()


def publish_command(client: mqtt.Client, action: str, payload: Dict[str, Any]):
    """Publish command to inverter-control"""
    if client:
        client.publish(
            "inverter/command",
            json.dumps({"action": action, **payload}),
            qos=0
        )


def get_state() -> Dict[str, Any]:
    """Get current state"""
    return current_state


def get_console() -> list:
    """Get console lines"""
    return console_lines
