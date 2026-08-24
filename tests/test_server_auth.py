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
