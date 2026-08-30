"""Tests for dbus-ev / dbus-evcharger EV topics in MqttState (Cerbo MQTT)."""

import json

import pytest

from inverter_dashboard import config
from inverter_dashboard.server import MqttState

PORTAL = "e1234567"


@pytest.fixture(name="portal_cfg")
def _portal_cfg(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", PORTAL)
    monkeypatch.setattr(config, "EV_INSTANCE", 22)
    monkeypatch.setattr(config, "EVCHARGER_INSTANCE", 40)
    return MqttState()


def _msg(value):
    return json.dumps({"value": value}).encode()


def test_vehicle_soc_sets_car_soc(portal_cfg):
    portal_cfg.handle_ev(f"N/{PORTAL}/ev/22/Soc", _msg(73.5))
    assert portal_cfg.current_state["car_soc"] == 73.5


def test_vehicle_ac_power_sets_ev_power(portal_cfg):
    portal_cfg.handle_ev(f"N/{PORTAL}/ev/22/Ac/Power", _msg(1500.0))
    assert portal_cfg.current_state["ev_power"] == 1500.0


def test_wallbox_power_sets_ev_charging_kw(portal_cfg):
    portal_cfg.handle_ev(f"N/{PORTAL}/evcharger/40/Ac/Power", _msg(7400))
    assert portal_cfg.current_state["ev_charging_kw"] == pytest.approx(7.4)


def test_other_portal_ignored(portal_cfg):
    portal_cfg.handle_ev("N/other/ev/22/Soc", _msg(50.0))
    portal_cfg.handle_ev("N/other/evcharger/40/Ac/Power", _msg(1000))
    assert portal_cfg.current_state == {}


def test_unrelated_instances_ignored(portal_cfg):
    portal_cfg.handle_ev(f"N/{PORTAL}/ev/99/Soc", _msg(50.0))
    portal_cfg.handle_ev(f"N/{PORTAL}/evcharger/77/Ac/Power", _msg(1000))
    assert portal_cfg.current_state == {}


def test_disabled_without_portal(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", "")
    state = MqttState()
    state.handle_ev(f"N/{PORTAL}/ev/22/Soc", _msg(50.0))
    assert state.current_state == {}


def test_bad_payload_ignored(portal_cfg):
    portal_cfg.handle_ev(f"N/{PORTAL}/ev/22/Soc", b"not json")
    portal_cfg.handle_ev(f"N/{PORTAL}/ev/22/Soc", json.dumps({"x": 1}).encode())
    assert portal_cfg.current_state == {}


def test_non_numeric_payload_ignored(portal_cfg):
    portal_cfg.handle_ev(f"N/{PORTAL}/ev/22/Soc", _msg("unknown"))
    assert portal_cfg.current_state == {}


def test_other_evcharger_paths_ignored(portal_cfg):
    # Only Ac/Power is mapped to ev_charging_kw; /Status, /Current, etc. are dropped.
    portal_cfg.handle_ev(f"N/{PORTAL}/evcharger/40/Status", _msg(2))
    portal_cfg.handle_ev(f"N/{PORTAL}/evcharger/40/Ac/Energy/Forward", _msg(12.3))
    assert portal_cfg.current_state == {}
