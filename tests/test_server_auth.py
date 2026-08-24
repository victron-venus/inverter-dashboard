"""Tests for root-page authentication and MQTT reconnect bookkeeping."""

import pytest
from fastapi.testclient import TestClient

from inverter_dashboard import config as cfg
from inverter_dashboard import server


@pytest.fixture
def client(monkeypatch):
    """TestClient with DASHBOARD_SECRET set (lifespan not started: no MQTT tasks)."""
    monkeypatch.setattr(server, "DASHBOARD_SECRET", "s3cret")
    return TestClient(server.app)


def test_index_requires_secret(client):
    resp = client.get("/")
    assert resp.status_code == 401


def test_index_rejects_wrong_credentials(client):
    assert client.get("/", params={"token": "nope"}).status_code == 403
    assert client.get("/", headers={"Authorization": "Bearer nope"}).status_code == 403


def test_index_accepts_valid_credentials(client):
    # static/dist is absent from the repo, so authorized requests reach the 404
    # fallback page — anything except 401/403 proves the auth layer passed.
    for resp in (
        client.get("/", params={"token": "s3cret"}),
        client.get("/", headers={"Authorization": "Bearer s3cret"}),
    ):
        assert resp.status_code not in (401, 403)


def test_index_open_when_no_secret_configured(monkeypatch):
    monkeypatch.setattr(server, "DASHBOARD_SECRET", "")
    resp = TestClient(server.app).get("/")
    assert resp.status_code not in (401, 403)


def test_api_state_reports_mqtt_health(client):
    data = client.get("/api/state").json()
    assert data["mqtt_connected"] is False
    assert data["mqtt_reconnects"] == 0


def test_next_backoff_doubles_and_caps(monkeypatch):
    monkeypatch.setattr(cfg, "MQTT_RECONNECT_MAX", 10.0)
    assert server._next_backoff(1.0) == 2.0
    assert server._next_backoff(5.0) == 10.0
    assert server._next_backoff(50.0) == 10.0


def test_solar_forecast_passthrough():
    from inverter_dashboard import websocket_handler as wsh

    wsh._state["mqtt_state"] = server.MqttState()
    wsh._state["mqtt_state"].current_state = {
        "solar_forecast": {"date": "2026-08-23", "today_kwh": 12.5, "tomorrow_kwh": 9.1}
    }
    payload = wsh.build_payload()
    assert payload["solar_forecast"]["today_kwh"] == 12.5
    wsh._state["mqtt_state"] = None


async def test_shutdown_mqtt_client_clears_state():
    server._app_state.mqtt_connected = True
    server._app_state.mqtt_client = object()
    await server._shutdown_mqtt_client()
    assert server._app_state.mqtt_client is None
    assert server._app_state.mqtt_connected is False


async def test_mqtt_loop_reconnects_after_broker_error(monkeypatch):
    """Broker death mid-session must not kill the loop: next attempt connects."""
    import asyncio

    from aiomqtt import MqttError

    attempts = {"n": 0}

    class FakeClient:
        """Minimal aiomqtt.Client stand-in: first session dies, second idles."""

        def __init__(self):
            attempts["n"] += 1
            self._n = attempts["n"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def subscribe(self, *args, **kwargs):
            return None

        @property
        def messages(self):
            async def gen():
                if self._n == 1:
                    raise MqttError("broker died")
                await asyncio.sleep(30)
                yield b""

            return gen()

    monkeypatch.setattr(cfg, "MQTT_RECONNECT_MIN", 0.01)
    monkeypatch.setattr(cfg, "MQTT_RECONNECT_MAX", 0.02)
    monkeypatch.setattr(server, "_make_mqtt_client", FakeClient)

    old_tasks = list(server._app_state.mqtt_tasks)
    server._app_state.mqtt_tasks.clear()
    task = None
    try:
        server._start_mqtt_client()
        task = server._app_state.mqtt_tasks[0]
        for _ in range(200):
            if server._app_state.mqtt_reconnects >= 1 and server._app_state.mqtt_connected:
                break
            await asyncio.sleep(0.02)
        assert server._app_state.mqtt_reconnects == 1
        assert server._app_state.mqtt_connected is True
        assert attempts["n"] == 2  # fresh client object per attempt
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        server._app_state.mqtt_tasks.clear()
        server._app_state.mqtt_tasks.extend(old_tasks)
        server._app_state.mqtt_connected = False
        server._app_state.mqtt_client = None
