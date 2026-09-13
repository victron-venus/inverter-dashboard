"""Gateway status and state snapshots reach clients in the same source transition."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from inverter_dashboard import gateway


@pytest.mark.asyncio
async def test_gateway_reports_connect_disconnect_and_recovery_before_emit(monkeypatch):
    app = SimpleNamespace(
        gateway_connected=False, mqtt_connected=False, gateway_polls=0, gateway_errors=0
    )
    initial = {"system": {"0/Ac/Grid/L1/Power": 100}}
    recovered = {"system": {"0/Ac/Grid/L1/Power": 200}}
    monkeypatch.setattr(
        gateway,
        "fetch_snapshot",
        AsyncMock(
            side_effect=[
                initial,
                RuntimeError("offline"),
                RuntimeError("still offline"),
                recovered,
                asyncio.CancelledError(),
            ]
        ),
    )
    monkeypatch.setattr(gateway.asyncio, "sleep", AsyncMock())
    events = []

    async def state_emit(snapshot):
        events.append((snapshot, app.gateway_connected, app.mqtt_connected))

    async def status_emit():
        events.append((None, app.gateway_connected, app.mqtt_connected))

    with pytest.raises(asyncio.CancelledError):
        await gateway.gateway_poll_loop(app, state_emit, status_emit)

    assert events == [(initial, True, True), (None, False, False), (recovered, True, True)]
    assert app.gateway_polls == 2
    assert app.gateway_errors == 2


@pytest.mark.asyncio
async def test_gateway_initial_failure_remains_disconnected_without_snapshot(monkeypatch):
    app = SimpleNamespace(
        gateway_connected=False, mqtt_connected=False, gateway_polls=0, gateway_errors=0
    )
    monkeypatch.setattr(
        gateway,
        "fetch_snapshot",
        AsyncMock(side_effect=[RuntimeError("offline"), asyncio.CancelledError()]),
    )
    monkeypatch.setattr(gateway.asyncio, "sleep", AsyncMock())
    state_emit, status_emit = AsyncMock(), AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await gateway.gateway_poll_loop(app, state_emit, status_emit)
    assert app.gateway_connected is False
    assert app.mqtt_connected is False
    state_emit.assert_not_called()
    status_emit.assert_not_called()
