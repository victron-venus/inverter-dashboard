"""Tests for dbus-pump water topics in MqttState (Cerbo MQTT)."""

import json

import pytest

from inverter_dashboard import config
from inverter_dashboard.server import MqttState

PORTAL = "e1234567"


@pytest.fixture(name="portal_cfg")
def _portal_cfg(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", PORTAL)
    monkeypatch.setattr(config, "WATER_TANK_INSTANCE", 21)
    monkeypatch.setattr(config, "WATER_PUMP_INSTANCE", 1)
    monkeypatch.setattr(config, "WATER_VALVE_INSTANCE", 2)
    return MqttState()


def _msg(value):
    return json.dumps({"value": value}).encode()


def test_tank_level_sets_water_level(portal_cfg):
    portal_cfg.handle_water(f"N/{PORTAL}/tank/21/Level", _msg(66.5))
    assert portal_cfg.current_state["water_level"] == 66.5


def test_valve_and_pump_states(portal_cfg):
    portal_cfg.handle_water(f"N/{PORTAL}/pump/2/State", _msg(1))
    portal_cfg.handle_water(f"N/{PORTAL}/pump/1/State", _msg(0))
    assert portal_cfg.current_state["water_valve"] is True
    assert portal_cfg.current_state["pump_switch"] is False


def test_other_portal_ignored(portal_cfg):
    portal_cfg.handle_water("N/other/tank/21/Level", _msg(10))
    portal_cfg.handle_water("N/other/pump/2/State", _msg(1))
    assert portal_cfg.current_state == {}


def test_unrelated_instances_ignored(portal_cfg):
    portal_cfg.handle_water(f"N/{PORTAL}/tank/9/Level", _msg(10))
    portal_cfg.handle_water(f"N/{PORTAL}/pump/startstop3/State", _msg(1))
    assert portal_cfg.current_state == {}


def test_disabled_without_portal(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", "")
    state = MqttState()
    state.handle_water(f"N/{PORTAL}/tank/21/Level", _msg(10))
    assert state.current_state == {}


def test_bad_payload_ignored(portal_cfg):
    portal_cfg.handle_water(f"N/{PORTAL}/tank/21/Level", b"not json")
    portal_cfg.handle_water(f"N/{PORTAL}/tank/21/Level", json.dumps({"x": 1}).encode())
    assert portal_cfg.current_state == {}
