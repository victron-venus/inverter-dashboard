"""Session extraction preserves real logging, exception context and lazy reads."""

import asyncio
import logging
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiomqtt import MqttError

from inverter_dashboard import server


@pytest.mark.parametrize("failure_type", [MqttError, RuntimeError, asyncio.CancelledError])
async def test_session_failure_logging_keeps_original_exception_context(
    monkeypatch, caplog, failure_type
):
    owner = server.AppState()
    trace = []
    failure = failure_type("original message failure")
    cause = ValueError("original cause")
    logger = logging.getLogger("tests.mqtt_session_errors")
    caplog.set_level(logging.WARNING, logger=logger.name)

    class TraceHandler(logging.Handler):
        def emit(self, record):
            trace.append(("log", owner.mqtt_reconnects, owner.mqtt_connected))

    handler = TraceHandler()
    logger.addHandler(handler)
    monkeypatch.setattr(server, "logger", logger)
    monkeypatch.setattr(server, "_app_state", owner)
    monkeypatch.setattr(server.websocket_handler, "set_mqtt_state", Mock())
    monkeypatch.setattr(server, "_subscribe_topics", AsyncMock())
    monkeypatch.setattr(server, "_push_observer", None)
    monkeypatch.setattr(server.gateway, "dual_path_enabled", lambda: True)
    monkeypatch.setattr(server.gateway, "gateway_configured", lambda: True)

    def disconnect(*_args):
        trace.append(("disconnect",))

    monkeypatch.setattr(
        server, "_push_service", SimpleNamespace(connect=Mock(), disconnect=disconnect)
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            trace.append(("exit",))
            return False

        @property
        def messages(self):
            return self

        def __aiter__(self):
            return self

        async def __anext__(self):
            # Let the real keepalive child start before injecting the failure.
            await asyncio.sleep(0)
            trace.append(("raise",))
            raise failure from cause

    async def keepalive(*_args):
        try:
            await asyncio.Event().wait()
        finally:
            trace.append(("keepalive_stopped",))

    async def failover(reason):
        assert sys.exc_info()[1] is failure
        trace.append(("failover", reason, owner.mqtt_reconnects, owner.mqtt_connected))
        await asyncio.sleep(0)
        assert sys.exc_info()[1] is failure
        trace.append(("failover_resumed",))

    async def emit():
        trace.append(("emit",))

    monkeypatch.setattr(server, "_make_mqtt_client", Client)
    monkeypatch.setattr(server, "_keepalive_loop", keepalive)
    monkeypatch.setattr(server, "_failover_to_igw", failover)
    server._start_mqtt_client()
    owner.mqtt_state._emit = emit
    task = owner.mqtt_tasks[0]
    try:
        if failure_type is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError) as raised:
                await asyncio.wait_for(task, 1)
            assert raised.value is failure
            assert owner.mqtt_reconnects == 0
            assert caplog.records == []
            assert trace == [
                ("raise",),
                ("exit",),
                ("disconnect",),
                ("emit",),
                ("keepalive_stopped",),
            ]
        else:
            await asyncio.wait_for(task, 1)
            assert len(caplog.records) == 1
            record = caplog.records[0]
            if failure_type is MqttError:
                assert record.levelno == logging.WARNING
                assert record.exc_info is None
                reason = "MQTT connection lost"
            else:
                assert record.levelno == logging.ERROR
                assert record.exc_info[0] is RuntimeError
                assert record.exc_info[1] is failure
                assert record.exc_info[1].__cause__ is cause
                frames = traceback.extract_tb(record.exc_info[2])
                assert frames[-1].name == "__anext__"
                assert frames[-1].line == "raise failure from cause"
                reason = "MQTT loop error"
            assert trace == [
                ("raise",),
                ("exit",),
                ("log", 1, True),
                ("failover", reason, 1, True),
                ("failover_resumed",),
                ("disconnect",),
                ("emit",),
                ("keepalive_stopped",),
            ]
        assert not owner.mqtt_connected
    finally:
        logger.removeHandler(handler)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("active", [False, True])
def test_portal_getter_checks_generation_before_reading(active):
    trace = []

    class State:
        @property
        def _portal_id(self):
            trace.append("portal")
            return "site"

    def current():
        trace.append("current")
        return active

    result = server._current_mqtt_portal(State(), current)
    assert result == ("site" if active else "")
    assert trace == (["current", "portal"] if active else ["current"])
