"""An MQTT session must join its keepalive even if state cleanup fails."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from inverter_dashboard import server


async def _running_session(monkeypatch):
    owner = server.AppState()
    monkeypatch.setattr(server, "_app_state", owner)
    monkeypatch.setattr(server.websocket_handler, "set_mqtt_state", Mock())
    monkeypatch.setattr(server.websocket_handler, "broadcast_state", AsyncMock())
    monkeypatch.setattr(server, "_subscribe_topics", AsyncMock())
    monkeypatch.setattr(server, "_push_observer", None)
    service = SimpleNamespace(connect=Mock(), disconnect=Mock())
    monkeypatch.setattr(server, "_push_service", service)
    finish_messages = asyncio.Event()
    keepalive_started = asyncio.Event()
    keepalive_stopped = asyncio.Event()
    children = []
    trace = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            trace.append("client_exit")
            return False

        @property
        def messages(self):
            return self

        def __aiter__(self):
            return self

        async def __anext__(self):
            await finish_messages.wait()
            raise StopAsyncIteration

    async def keepalive(_client, _portal_getter):
        children.append(asyncio.current_task())
        keepalive_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            trace.append("keepalive_stopped")
            keepalive_stopped.set()

    monkeypatch.setattr(server, "_make_mqtt_client", Client)
    monkeypatch.setattr(server, "_keepalive_loop", keepalive)
    server._start_mqtt_client()
    task = owner.mqtt_tasks[0]
    await asyncio.wait_for(keepalive_started.wait(), 1)
    return SimpleNamespace(
        owner=owner,
        service=service,
        finish_messages=finish_messages,
        keepalive_stopped=keepalive_stopped,
        children=children,
        task=task,
        trace=trace,
    )


async def _stop_test_tasks(session):
    tasks = [session.task, *session.children]
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("stage", ["disconnect", "clear_daemon", "clear_cerbo", "emit"])
@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
async def test_cleanup_failure_joins_keepalive_and_preserves_error(
    monkeypatch, stage, failure_type
):
    session = await _running_session(monkeypatch)
    failure = failure_type("state cleanup failed")

    def fail(*_args):
        session.trace.append(stage)
        raise failure

    async def fail_emit():
        fail()

    if stage == "disconnect":
        session.service.disconnect = fail
    elif stage == "clear_daemon":
        session.owner.mqtt_state.clear_daemon_state = fail
    elif stage == "clear_cerbo":
        session.owner.mqtt_state.clear_cerbo_state = fail
    else:
        session.owner.mqtt_state._emit = fail_emit

    try:
        session.finish_messages.set()
        with pytest.raises(failure_type, match="state cleanup failed") as raised:
            await asyncio.wait_for(session.task, 1)
        assert raised.value is failure
        assert session.keepalive_stopped.is_set()
        assert all(task.done() for task in session.children)
        assert session.trace == ["client_exit", stage, "keepalive_stopped"]
        assert not session.owner.mqtt_connected
    finally:
        await _stop_test_tasks(session)


async def test_cancellation_during_cleanup_emit_joins_keepalive(monkeypatch):
    session = await _running_session(monkeypatch)
    emitting = asyncio.Event()

    async def blocked_emit():
        session.trace.append("emit")
        emitting.set()
        await asyncio.Event().wait()

    session.owner.mqtt_state._emit = blocked_emit
    try:
        session.finish_messages.set()
        await asyncio.wait_for(emitting.wait(), 1)
        session.task.cancel("stop MQTT session")
        with pytest.raises(asyncio.CancelledError, match="stop MQTT session"):
            await session.task
        assert session.keepalive_stopped.is_set()
        assert all(task.done() for task in session.children)
        assert session.trace == ["client_exit", "emit", "keepalive_stopped"]
    finally:
        await _stop_test_tasks(session)
