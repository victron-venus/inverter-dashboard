"""Explicit unknown observations invalidate their existing native fields."""

import pytest

from inverter_dashboard import cerbo, config


@pytest.fixture
def state(monkeypatch):
    for name, value in {
        "WATER_TANK_INSTANCE": 21,
        "EV_INSTANCE": 22,
        "EVCHARGER_INSTANCE": 40,
        "WATER_PUMP_INSTANCE": 23,
        "WATER_VALVE_INSTANCE": 24,
    }.items():
        monkeypatch.setattr(config, name, value)
    result = cerbo.CerboOverlayMixin()
    result._init_cerbo()
    result._cerbo_claimed_keys.add("existing")
    return result


@pytest.mark.parametrize(
    "kind,instance,path,fields",
    [
        ("grid", "0", "Ac/L1/Power", {"g1", "gt"}),
        ("grid", "0", "Ac/Power", {"gt"}),
        ("vebus", "0", "Ac/ActiveIn/L2/P", {"g2", "gt"}),
        ("system", "0", "Ac/Consumption/L3/Power", {"t3", "tt"}),
        ("system", "0", "Ac/Grid/L2/Power", {"g2", "gt"}),
        ("system", "0", "Dc/Battery/Voltage", {"battery_soc", "battery_voltage", "bv"}),
        ("battery", "0", "Dc/0/Voltage", {"battery_soc", "battery_voltage", "bv"}),
        ("battery", "0", "Soc", {"battery_soc"}),
        ("pump", "23", "Mode", {"pump_mode", "water_pump_mode"}),
        ("pump", "24", "Mode", {"water_valve_mode"}),
        ("pump", "23", "State", {"pump_switch"}),
        ("pump", "24", "State", {"water_valve"}),
        ("solarcharger", "0", "Yield/Power", {"mppt_total", "pv_total", "solar_total"}),
        ("system", "0", "Dc/Pv/Power", {"mppt_total", "pv_total", "solar_total"}),
        ("pvinverter", "0", "Ac/L3/Power", {"pv_inverter_total", "solar_total"}),
        ("system", "0", "Ac/PvOnGrid/L1/Power", {"pv_inverter_total", "solar_total"}),
        ("vebus", "0", "State", {"inverter_state"}),
        ("vebus", "0", "Hub4/L1/AcPowerSetpoint", {"setpoint"}),
        ("settings", "0", "Settings/CGwacs/Hub4Mode", {"ess_mode"}),
        ("ev", "22", "Soc", {"car_soc"}),
        ("evcharger", "40", "Ac/Power", {"ev_charging_kw", "ev_charging_power"}),
        ("tank", "21", "Level", {"water_level"}),
        ("unknown", "0", "Soc", set()),
        ("battery", "0", "CustomName", set()),
    ],
)
def test_unknown_leaf_claims_only_its_fields(state, kind, instance, path, fields):
    state._claim_invalid_leaf(kind, instance, path)
    assert state._cerbo_claimed_keys == fields | {"existing"}


@pytest.mark.parametrize("kind,path", [("tank", "Level"), ("ev", "Soc"), ("evcharger", "Ac/Power")])
def test_unselected_device_cannot_invalidate_selected_fields(state, kind, path):
    state._claim_invalid_leaf(kind, "99", path)
    assert state._cerbo_claimed_keys == {"existing"}


def test_same_pump_and_valve_instance_preserves_pump_precedence(state, monkeypatch):
    monkeypatch.setattr(config, "WATER_VALVE_INSTANCE", 23)
    state._claim_invalid_leaf("pump", "23", "Mode")
    assert state._cerbo_claimed_keys == {"existing", "pump_mode", "water_pump_mode"}
