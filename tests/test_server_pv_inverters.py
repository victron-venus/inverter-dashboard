"""Tests for GX PV-inverter topics in MqttState (Cerbo MQTT, any vendor)."""

import json

from inverter_dashboard.server import MqttState


def _msg(value):
    return json.dumps({"value": value}).encode()


def test_power_voltage_current_and_name_collected():
    ms = MqttState()
    assert ms._handle_pvinverter("N/p1/pvinverter/369/Ac/Power", _msg(163)) is True
    assert ms._handle_pvinverter("N/p1/pvinverter/369/Ac/L1/Voltage", _msg(126.0)) is True
    assert ms._handle_pvinverter("N/p1/pvinverter/369/Ac/L1/Current", _msg(1.29)) is True
    assert ms._handle_pvinverter("N/p1/pvinverter/369/ProductName", _msg("Tasmota PV 1")) is True
    assert ms.current_state["pv_inverters"] == [
        {
            "instance": "369",
            "power": 163.0,
            "voltage": 126.0,
            "current": 1.29,
            "name": "Tasmota PV 1",
        }
    ]


def test_instances_sorted_numerically():
    ms = MqttState()
    ms._handle_pvinverter("N/p1/pvinverter/9895/Ac/Power", _msg(181))
    ms._handle_pvinverter("N/p1/pvinverter/369/Ac/Power", _msg(163))
    assert [inv["power"] for inv in ms.current_state["pv_inverters"]] == [163.0, 181.0]


def test_unknown_path_and_bad_payload_ignored():
    ms = MqttState()
    assert ms._handle_pvinverter("N/p1/pvinverter/369/StatusCode", _msg(0)) is False
    assert ms._handle_pvinverter("N/p1/pvinverter/369/Ac/Power", b"not json") is False
    assert "pv_inverters" not in ms.current_state


def test_on_message_routes_pvinverter_and_fires_callback():
    import asyncio

    ms = MqttState()
    fired = []

    async def cb():
        fired.append(True)

    ms.set_state_callback(cb)
    asyncio.run(ms.on_message("N/p1/pvinverter/369/Ac/L1/Voltage", _msg(126.0)))
    assert fired == [True]
    assert ms.current_state["pv_inverters"] == [{"instance": "369", "voltage": 126.0}]


def test_custom_name_and_totals():
    ms = MqttState()
    assert ms._handle_pvinverter("N/p1/pvinverter/10/Ac/Power", _msg(100)) is True
    assert ms._handle_pvinverter("N/p1/pvinverter/10/CustomName", _msg("Shed PV")) is True
    assert ms._handle_pvinverter("N/p1/pvinverter/20/Ac/L1/Power", _msg(50)) is True
    assert ms.current_state["pv_inverters"][0]["name"] == "Shed PV"
    assert ms.current_state["pv_inverter_individual"] == [100.0, 50.0]
    assert ms.current_state["pv_inverter_total"] == 150.0
    assert ms.current_state["solar_total"] == 150.0


def test_booleans_coerced_from_daemon():
    ms = MqttState()
    ms._merge_daemon_state(
        {
            "booleans": {"only_charging": "true", "minimize_charging": 0, "no_feed": True},
            "daily_stats": {"produced_yesterday": 12.5},
            "solar_forecast": {"today_kwh": 20.0, "tomorrow_kwh": 18.0},
        }
    )
    assert ms.current_state["booleans"]["only_charging"] is True
    assert ms.current_state["booleans"]["minimize_charging"] is False
    assert ms.current_state["booleans"]["no_feed"] is True
    assert ms.current_state["daily_stats"]["produced_yesterday"] == 12.5
    assert ms.current_state["solar_forecast"]["today_kwh"] == 20.0
