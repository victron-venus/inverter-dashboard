"""Cerbo-first live telemetry: system/battery/solar/loads survive slim inverter/state."""

import asyncio
import json

import pytest

from inverter_dashboard import config
from inverter_dashboard.cerbo import voltage_soc
from inverter_dashboard.server import MqttState


def _msg(value):
    return json.dumps({"value": value}).encode()


@pytest.fixture(name="ms")
def _ms():
    return MqttState()


def test_voltage_soc_matches_desktop():
    assert voltage_soc(40.0) == 0.0
    assert voltage_soc(54.4) == 100.0
    assert voltage_soc(47.2) == 50.0


def test_slim_inverter_state_does_not_clear_cerbo_loads(ms):
    assert ms._handle_acload("N/p1/acload/81/Ac/Power", _msg(420)) is True
    assert ms._handle_acload("N/p1/acload/81/CustomName", _msg("Oven")) is True
    assert ms.current_state["loads"]["Oven"] == 420.0

    ms._merge_daemon_state({"daily_stats": {"solar": 12.5}, "version": "9.9.9"})
    assert ms.current_state["loads"]["Oven"] == 420.0
    assert ms.current_state["daily_stats"]["solar"] == 12.5
    assert ms.current_state["version"] == "9.9.9"


def test_acload_power_updates_existing_entry(ms):
    ms._handle_acload("N/p1/acload/81/Ac/Power", _msg(100))
    ms._handle_acload("N/p1/acload/81/CustomName", _msg("Oven"))
    ms._handle_acload("N/p1/acload/81/Ac/Power", _msg(455))
    assert ms.current_state["loads"]["Oven"] == 455.0


def test_systemcalc_grid_and_consumption(ms):
    assert ms._handle_cerbo_device("N/p1/system/0/Ac/Grid/L1/Power", _msg(100)) is True
    assert ms._handle_cerbo_device("N/p1/system/0/Ac/Grid/L2/Power", _msg(50)) is True
    assert ms._handle_cerbo_device("N/p1/system/0/Ac/Consumption/L1/Power", _msg(200)) is True
    assert ms._handle_cerbo_device("N/p1/system/0/Ac/Consumption/L2/Power", _msg(80)) is True
    assert ms.current_state["g1"] == 100.0
    assert ms.current_state["g2"] == 50.0
    assert ms.current_state["gt"] == 150.0
    assert ms.current_state["tt"] == 280.0

    # Slim daemon tick must not wipe Cerbo grid/consumption
    ms._merge_daemon_state({"daily_stats": {"battery_out": 3.1}})
    assert ms.current_state["gt"] == 150.0
    assert ms.current_state["tt"] == 280.0
    assert ms.current_state["daily_stats"]["battery_out"] == 3.1


def test_battery_shunt_voltage_soc(ms):
    assert ms._handle_cerbo_device("N/p1/battery/512/ProductName", _msg("SmartShunt 500A")) is True
    assert ms._handle_cerbo_device("N/p1/battery/512/Dc/0/Voltage", _msg(47.2)) is True
    assert ms._handle_cerbo_device("N/p1/battery/512/Dc/0/Current", _msg(-12.5)) is True
    assert ms._handle_cerbo_device("N/p1/battery/512/Dc/0/Power", _msg(-590)) is True
    assert ms.current_state["battery_soc"] == 50.0
    assert ms.current_state["battery_voltage"] == 47.2
    assert ms.current_state["battery_current"] == -12.5
    assert ms.current_state["battery_power"] == -590.0
    assert ms.current_state["batteries"][0]["state"] == "Discharging"


def test_solarcharger_and_pv_make_solar_total(ms):
    assert ms._handle_cerbo_device("N/p1/solarcharger/1/Yield/Power", _msg(300)) is True
    assert ms._handle_pvinverter("N/p1/pvinverter/369/Ac/Power", _msg(200)) is True
    assert ms.current_state["mppt_total"] == 300.0
    assert ms.current_state["solar_total"] == 500.0


def test_vebus_setpoint_and_mode(ms):
    assert ms._handle_cerbo_device("N/p1/vebus/276/Hub4/L1/AcPowerSetpoint", _msg(-500)) is True
    assert ms._handle_cerbo_device("N/p1/vebus/276/State", _msg(9)) is True
    assert ms.current_state["setpoint"] == -500.0
    assert ms.current_state["inverter_state"] == "Inverting"


def test_on_message_slim_state_preserves_loads_and_emits():
    ms = MqttState()
    fired = []

    async def cb():
        fired.append(True)

    ms.set_state_callback(cb)

    async def run():
        await ms.on_message("N/p1/acload/81/Ac/Power", _msg(90))
        await ms.on_message("N/p1/acload/81/CustomName", _msg("Dryer"))
        await ms.on_message(
            "inverter/state",
            json.dumps({"daily_stats": {"solar": 1.0}, "booleans": {"no_feed": False}}).encode(),
        )

    asyncio.run(run())
    assert ms.current_state["loads"]["Dryer"] == 90.0
    assert ms.current_state["daily_stats"]["solar"] == 1.0
    assert len(fired) >= 2


def test_portal_discovery_updates_id(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", "")
    ms = MqttState()
    assert ms._portal_id == ""
    seen = []

    async def on_portal(portal: str):
        seen.append(portal)

    ms.set_portal_callback(on_portal)

    async def run():
        await ms.on_message("inverter/portal", b"b827ebea1ece")

    asyncio.run(run())
    assert ms._portal_id == "b827ebea1ece"
    assert seen == ["b827ebea1ece"]
