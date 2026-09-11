"""Unit tests for inverter-gateway (IGW) snapshot mapping + data-source policy."""

from __future__ import annotations

import pytest

from inverter_dashboard import config, gateway
from inverter_dashboard.server import MqttState


@pytest.fixture(name="ms")
def _ms():
    return MqttState()


@pytest.fixture(autouse=True)
def _reset_active_source():
    gateway.set_active_source("none", dual_path=False)
    yield
    gateway.set_active_source("none", dual_path=False)


def test_prefer_gateway_igw_only_before_selection(monkeypatch):
    """Before lifespan sets active_source, IGW-only configs prefer gateway."""
    monkeypatch.setattr(config, "GATEWAY_ENABLED", True)
    monkeypatch.setattr(config, "GATEWAY_URL", "https://victron.example")
    monkeypatch.setattr(config, "MQTT_HOST", "")
    assert gateway.gateway_configured() is True
    assert gateway.prefer_gateway() is True


def test_both_configured_does_not_abandon_mqtt_before_selection(monkeypatch):
    """Coexistence: GATEWAY + MQTT_HOST must not force IGW-only before probe."""
    monkeypatch.setattr(config, "GATEWAY_ENABLED", True)
    monkeypatch.setattr(config, "GATEWAY_URL", "https://victron.example")
    monkeypatch.setattr(config, "MQTT_HOST", "192.168.160.150")
    assert gateway.gateway_configured() is True
    assert gateway.mqtt_configured() is True
    assert gateway.prefer_gateway() is False


def test_prefer_mqtt_when_gateway_off(monkeypatch):
    monkeypatch.setattr(config, "GATEWAY_ENABLED", False)
    monkeypatch.setattr(config, "GATEWAY_URL", "")
    monkeypatch.setattr(config, "MQTT_HOST", "Cerbo")
    assert gateway.prefer_gateway() is False
    assert gateway.mqtt_configured() is True


def test_prefer_gateway_follows_active_source(monkeypatch):
    monkeypatch.setattr(config, "GATEWAY_ENABLED", True)
    monkeypatch.setattr(config, "GATEWAY_URL", "https://victron.example")
    monkeypatch.setattr(config, "MQTT_HOST", "Cerbo")
    gateway.set_active_source("igw", dual_path=True)
    assert gateway.prefer_gateway() is True
    gateway.set_active_source("mqtt", dual_path=True)
    assert gateway.prefer_gateway() is False


def test_choose_startup_source_mqtt_first_when_reachable():
    assert (
        gateway.choose_startup_source(
            mqtt_configured=True, igw_configured=True, mqtt_reachable=True
        )
        == "mqtt"
    )
    assert (
        gateway.choose_startup_source(
            mqtt_configured=True, igw_configured=True, mqtt_reachable=False
        )
        == "igw"
    )
    assert (
        gateway.choose_startup_source(
            mqtt_configured=True, igw_configured=False, mqtt_reachable=False
        )
        == "mqtt"
    )
    assert (
        gateway.choose_startup_source(
            mqtt_configured=False, igw_configured=True, mqtt_reachable=False
        )
        == "igw"
    )
    assert (
        gateway.choose_startup_source(
            mqtt_configured=False, igw_configured=False, mqtt_reachable=False
        )
        == "none"
    )


@pytest.mark.asyncio
async def test_probe_mqtt_reachable_empty_host(monkeypatch):
    monkeypatch.setattr(config, "MQTT_HOST", "")
    assert await gateway.probe_mqtt_reachable() is False


@pytest.mark.asyncio
async def test_probe_mqtt_reachable_tcp_ok(monkeypatch):
    class _W:
        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def _open(host, port):
        assert host == "broker.local"
        assert port == 1883
        return (None, _W())

    monkeypatch.setattr(config, "MQTT_HOST", "broker.local")
    monkeypatch.setattr(config, "MQTT_PORT", 1883)
    monkeypatch.setattr(gateway.asyncio, "open_connection", _open)
    assert await gateway.probe_mqtt_reachable(timeout=1.0) is True


@pytest.mark.asyncio
async def test_probe_mqtt_reachable_tcp_fail(monkeypatch):
    async def _open(host, port):
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr(config, "MQTT_HOST", "broker.local")
    monkeypatch.setattr(config, "MQTT_PORT", 1883)
    monkeypatch.setattr(gateway.asyncio, "open_connection", _open)
    assert await gateway.probe_mqtt_reachable(timeout=1.0) is False


def test_apply_snapshot_maps_live_tiles(ms):
    snap = {
        "system": {
            "0/Ac/Grid/L1/Power": 10.0,
            "0/Ac/Grid/L2/Power": 5.0,
            "0/Ac/Consumption/L1/Power": 100.0,
            "0/Ac/Consumption/L2/Power": 20.0,
        },
        "battery": {
            "512/CustomName": "SmartShunt 500A",
            "512/Dc/0/Voltage": 47.2,
            "512/Dc/0/Current": -12.5,
            "512/Dc/0/Power": -590.0,
        },
        "solarcharger": {
            "290/Yield/Power": 300.0,
            "290/ProductName": "MPPT",
        },
        "pvinverter": {
            "369/Ac/Power": 200.0,
            "369/CustomName": "Tasmota",
        },
        "vebus": {
            "290/Hub4/L1/AcPowerSetpoint": -500.0,
            "290/State": 9,
        },
        "acload": {
            "81/Ac/Power": 420.0,
            "81/CustomName": "Oven",
        },
        "tank": {"21/Level": 91.0},
        "pump": {},
        "ev": {"22/Ac/Power": 0.0},
        "evcharger": {"40/Ac/Power": 1500.0},
    }
    gateway.apply_snapshot(ms, snap)
    assert ms.current_state["g1"] == 10.0
    assert ms.current_state["gt"] == 15.0
    assert ms.current_state["tt"] == 120.0
    assert ms.current_state["battery_soc"] == 50.0
    assert ms.current_state["battery_power"] == -590.0
    assert ms.current_state["mppt_total"] == 300.0
    assert ms.current_state["solar_total"] == 500.0
    assert ms.current_state["setpoint"] == -500.0
    assert ms.current_state["inverter_state"] == "Inverting"
    assert ms.current_state["loads"]["Oven"] == 420.0
    assert ms.current_state["water_level"] == 91.0
    assert ms.current_state["ev_charging_kw"] == 1.5


def test_tank_fraction_normalized(ms):
    gateway.apply_snapshot(ms, {"tank": {"21/Level": 0.42}})
    assert ms.current_state["water_level"] == pytest.approx(42.0)


def test_build_headers_includes_cf_and_bearer(monkeypatch):
    monkeypatch.setattr(config, "GATEWAY_ACCESS_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "GATEWAY_ACCESS_CLIENT_SECRET", "csec")
    monkeypatch.setattr(config, "GATEWAY_API_TOKEN", "tok")
    h = gateway.build_headers()
    assert h["CF-Access-Client-Id"] == "cid"
    assert h["CF-Access-Client-Secret"] == "csec"
    assert h["Authorization"] == "Bearer tok"
