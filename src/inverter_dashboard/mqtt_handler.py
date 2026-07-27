"""
Async MQTT client handler using aiomqtt for non-blocking I/O in FastAPI event loop
"""

import asyncio
import json
import logging
from typing import Any, Callable
from aiomqtt import Client, MqttError

from . import config

logger = logging.getLogger(__name__)


class MqttState:
    """Encapsulated MQTT state and connection management."""

    def __init__(self) -> None:
        self.current_state: dict[str, Any] = {}
        self.console_lines: list[str] = []
        self._on_state_update: Callable | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def set_state_callback(self, callback: Callable, loop: asyncio.AbstractEventLoop) -> None:
        """Set callback to be called when state updates"""
        self._on_state_update = callback
        self._main_loop = loop

    def on_message(self, topic: str, payload: bytes) -> None:
        """Process incoming MQTT message"""
        try:
            if topic == "inverter/state":
                self.current_state = json.loads(payload.decode())
                if self._on_state_update and self._main_loop and self._main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._on_state_update(), self._main_loop)

            elif topic == "inverter/console":
                line = payload.decode()
                self.console_lines.append(line)
                if len(self.console_lines) > config.CONSOLE_MAX_LINES:
                    self.console_lines.pop(0)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.exception("MQTT message parse error: %s", e)
        except Exception as e:
            logger.exception("MQTT message error: %s", e)

    def get_state(self) -> dict[str, Any]:
        """Get current state"""
        return self.current_state

    def get_console(self) -> list[str]:
        """Get console lines"""
        return self.console_lines


class AsyncMqttClient:
    """Async MQTT client using aiomqtt with automatic reconnection."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        state: MqttState,
        *,
        host: str = config.MQTT_HOST,
        port: int = config.MQTT_PORT,
        username: str | None = config.MQTT_USERNAME,
        password: str | None = config.MQTT_PASSWORD,
        tls: bool = config.MQTT_TLS,
        ca_cert: str | None = config.MQTT_CA_CERT,
    ) -> None:
        self.state = state
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.tls = tls
        self.ca_cert = ca_cert

        self._client: Client | None = None
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def connect(self) -> None:
        """Connect to MQTT broker"""
        if self._client is not None:
            return

        logger.info("Connecting to MQTT broker at %s:%s", self.host, self.port)

        self._client = Client(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            tls_params=({"ca_certs": self.ca_cert} if self.tls and self.ca_cert else {}) if self.tls else None,
            tls_insecure=self.tls and not self.ca_cert,
        )

        await self._client
        logger.info("Connected to MQTT broker")

        # Subscribe to topics
        await self._client.subscribe("inverter/state")
        await self._client.subscribe("inverter/console")
        logger.info("Subscribed to MQTT topics")

    async def start(self) -> None:
        """Start the message processing loop"""
        if self._running:
            return

        await self.connect()
        self._running = True

        # Start message handler task
        task = asyncio.create_task(self._message_loop())
        self._tasks.append(task)

    async def _message_loop(self) -> None:
        """Process incoming messages"""
        if self._client is None:
            logger.error("MQTT client not initialized")
            return
        try:
            async for message in self._client.messages:
                self.state.on_message(message.topic.value, message.payload)
        except MqttError as e:
            logger.error("MQTT message loop error: %s", e)
            if self._running:
                await self._reconnect()
        except Exception as e:  # pylint: disable=broad-except
            if isinstance(e, asyncio.CancelledError):
                raise
            logger.exception("Unexpected error in message loop: %s", e)
            if self._running:
                await self._reconnect()

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff"""
        delay = 1
        max_delay = 60

        while self._running:
            logger.info("Attempting to reconnect in %ds...", delay)
            await asyncio.sleep(delay)

            try:
                await self.connect()
                logger.info("Reconnected to MQTT broker")
                return
            except MqttError as e:
                logger.warning("Reconnection failed: %s", e)
                delay = min(delay * 2, max_delay)

    async def publish(self, action: str, payload: dict[str, Any] | None = None) -> None:
        """Publish command to inverter-control"""
        if self._client is None:
            logger.warning("Cannot publish: MQTT client not connected")
            return

        topic = f"inverter/cmd/{action}"
        message = json.dumps(payload) if payload else ""

        try:
            await self._client.publish(topic, message, qos=0)
            logger.debug("Published command to %s", topic)
        except MqttError as e:
            logger.error("Failed to publish to %s: %s", topic, e)

    async def stop(self) -> None:
        """Stop the client and cleanup"""
        self._running = False

        # Cancel message loop task
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()

        # Close client
        if self._client:
            try:
                await self._client
            except Exception as e:  # pylint: disable=broad-except
                logger.exception("Error closing MQTT client: %s", e)
            self._client = None

        logger.info("MQTT client stopped")


# Global client reference (for backward compatibility with publish_command)
_async_client: AsyncMqttClient | None = None


def create_client(state: MqttState) -> AsyncMqttClient:
    """Create async MQTT client - backward compatible interface"""
    global _async_client  # pylint: disable=global-statement
    _async_client = AsyncMqttClient(state)
    return _async_client


async def start_client(client: AsyncMqttClient) -> None:
    """Start async MQTT client - backward compatible interface"""
    await client.start()


async def stop_client(client: AsyncMqttClient) -> None:
    """Stop async MQTT client - backward compatible interface"""
    await client.stop()


async def publish_command(client: AsyncMqttClient, action: str, payload: dict[str, Any] | None = None) -> None:
    """Publish command - backward compatible interface (now async)"""
    await client.publish(action, payload)
