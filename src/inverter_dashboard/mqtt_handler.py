"""
Async MQTT client handler using aiomqtt for non-blocking I/O in FastAPI event loop
"""

import asyncio
import json
import logging
from typing import Any, Callable
from aiomqtt import Client, MqttError, TLSParameters

from . import config

logger = logging.getLogger(__name__)


class MqttState:
    """Encapsulated MQTT state and connection management."""

    def __init__(self) -> None:
        self.current_state: dict[str, Any] = {}
        self.console_lines: list[str] = []
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

        tls_params = None
        tls_insecure = False
        if self.tls:
            if self.ca_cert:
                tls_params = TLSParameters(ca_certs=self.ca_cert)
            else:
                tls_params = TLSParameters()
                tls_insecure = True

        self._client = Client(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            tls_params=tls_params,
            tls_insecure=tls_insecure,
        )

        await self._client.__aenter__()  # pylint: disable=unnecessary-dunder-call
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
                await self.state.on_message(message.topic.value, message.payload)
        except MqttError as e:
            logger.exception("MQTT message loop error: %s", e)
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

        # Drop the dead client so connect() actually reconnects
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:  # pylint: disable=broad-except
                pass
        self._client = None

        while self._running:
            logger.info("Attempting to reconnect in %ds...", delay)
            await asyncio.sleep(delay)

            try:
                await self.connect()
                logger.info("Reconnected to MQTT broker")
                # Resume processing messages on the new connection
                self._tasks = [task for task in self._tasks if not task.done()]
                self._tasks.append(asyncio.create_task(self._message_loop()))
                return
            except MqttError as e:
                logger.warning("Reconnection failed: %s", e)
                self._client = None
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
            logger.exception("Failed to publish to %s: %s", topic, e)

    async def stop(self) -> None:
        """Stop the client and cleanup"""
        self._running = False

        # Cancel message loop task
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()

        # Close client
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)  # pylint: disable=unnecessary-dunder-call
            except Exception as e:  # pylint: disable=broad-except
                logger.exception("Error closing MQTT client: %s", e)
            finally:
                self._client = None

        logger.info("MQTT client stopped")
