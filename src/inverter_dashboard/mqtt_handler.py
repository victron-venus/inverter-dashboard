"""
MQTT client handler for receiving state from Cerbo
"""

import json
import asyncio
import logging
from typing import Dict, Any, Callable

import paho.mqtt.client as mqtt

from . import config

logger = logging.getLogger(__name__)


class MqttState:
    """Encapsulated MQTT state and connection management."""

    def __init__(self) -> None:
        self.current_state: Dict[str, Any] = {}
        self.console_lines: list[str] = []
        self._on_state_update: Callable | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def set_state_callback(self, callback: Callable, loop: asyncio.AbstractEventLoop) -> None:
        """Set callback to be called when state updates"""
        self._on_state_update = callback
        self._main_loop = loop

    def on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any, rc: Any, _properties: Any = None) -> None:
        """MQTT connected - subscribe to topics"""
        logger.info("MQTT connected to %s:%s", config.MQTT_HOST, config.MQTT_PORT)
        client.subscribe("inverter/state")
        client.subscribe("inverter/console")

    def on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        """MQTT message received"""
        try:
            if msg.topic == "inverter/state":
                self.current_state = json.loads(msg.payload.decode())
                if self._on_state_update and self._main_loop and self._main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._on_state_update(), self._main_loop)

            elif msg.topic == "inverter/console":
                line = msg.payload.decode()
                self.console_lines.append(line)
                if len(self.console_lines) > config.CONSOLE_MAX_LINES:
                    self.console_lines.pop(0)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.exception("MQTT message parse error: %s", e)
        except Exception as e:
            logger.exception("MQTT message error: %s", e)

    def get_state(self) -> Dict[str, Any]:
        """Get current state"""
        return self.current_state

    def get_console(self) -> list[str]:
        """Get console lines"""
        return self.console_lines


# Module-level singleton
_state = MqttState()


def set_state_callback(callback: Callable, loop: asyncio.AbstractEventLoop):
    """Set callback to be called when state updates"""
    _state.set_state_callback(callback, loop)


def on_connect(client, userdata, flags, rc, properties=None):
    """MQTT connected - subscribe to topics"""
    _state.on_connect(client, userdata, flags, rc, properties)


def on_message(client, userdata, msg):
    """MQTT message received"""
    _state.on_message(client, userdata, msg)


def create_client() -> mqtt.Client:
    """Create and configure MQTT client"""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    if config.MQTT_USERNAME:
        client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD or None)
        logger.info("MQTT auth configured (username: %s)", config.MQTT_USERNAME)

    if config.MQTT_TLS:
        if config.MQTT_CA_CERT:
            client.tls_set(ca_certs=config.MQTT_CA_CERT)
        else:
            client.tls_set()
        client.tls_insecure(True)
        logger.info("MQTT TLS enabled")

    return client


def start_client(client: mqtt.Client):
    """Start MQTT client connection"""
    try:
        client.connect(config.MQTT_HOST, config.MQTT_PORT, 60)
        client.loop_start()
        logger.info("MQTT client started, connecting to %s:%s", config.MQTT_HOST, config.MQTT_PORT)
    except Exception as e:
        logger.exception("MQTT connection failed: %s", e)


def stop_client(client: mqtt.Client):
    """Stop MQTT client"""
    if client:
        client.loop_stop()
        client.disconnect()


def publish_command(client: mqtt.Client, action: str, payload: Dict[str, Any]):
    """Publish command to inverter-control"""
    if client:
        client.publish(
            f"inverter/cmd/{action}",
            json.dumps(payload) if payload else "",
            qos=0
        )


def get_state() -> Dict[str, Any]:
    """Get current state"""
    return _state.get_state()


def get_console() -> list:
    """Get console lines"""
    return _state.get_console()
