"""Tests for inverter/notifications and Victron alarm handling."""

import json

from inverter_dashboard import server


async def test_push_notification_from_control():
    ms = server.MqttState()
    await ms.on_message(
        "inverter/notifications",
        json.dumps({"id": "n1", "level": "warning", "title": "T", "body": "B"}).encode(),
    )
    assert len(ms.get_notifications()) == 1
    n = ms.get_notifications()[0]
    assert n["id"] == "n1"
    assert n["source"] == "inverter-control"  # default filled


async def test_notification_list_capped():
    ms = server.MqttState()
    for i in range(server.MqttState.NOTIFICATIONS_MAX + 10):
        await ms.on_message(
            "inverter/notifications",
            json.dumps({"id": f"n{i}", "level": "info", "title": "x", "body": ""}).encode(),
        )
    assert len(ms.get_notifications()) == server.MqttState.NOTIFICATIONS_MAX
    # Oldest 10 dropped, newest 100 kept
    assert ms.get_notifications()[0]["id"] == "n10"
    assert ms.get_notifications()[-1]["id"] == "n109"


async def test_victron_alarm_transitions():
    ms = server.MqttState()
    topic = "N/portal/battery_512/Alarms/HighCellVoltage"

    await ms.on_message(topic, b'{"value": 2}')
    notifs = ms.get_notifications()
    assert len(notifs) == 1
    assert notifs[0]["level"] == "alarm"
    assert notifs[0]["title"] == "Battery 512"
    assert notifs[0]["body"] == "High Cell Voltage: Alarm"
    assert notifs[0]["id"] == f"victron-{topic}"

    # Same value again -> no duplicate
    await ms.on_message(topic, b'{"value": 2}')
    assert len(ms.get_notifications()) == 1

    # Warning transition replaces nothing but appends (value changed)
    await ms.on_message(topic, b'{"value": 1}')
    assert len(ms.get_notifications()) == 2
    assert ms.get_notifications()[-1]["level"] == "warning"

    # Cleared -> banner notifications for this topic removed
    await ms.on_message(topic, b'{"value": 0}')
    assert all(n["id"] != f"victron-{topic}" for n in ms.get_notifications())
    assert len(ms.get_notifications()) == 0


def test_pretty_names():
    assert server.pretty_service_name("battery_512") == "Battery 512"
    assert server.pretty_service_name("vebus") == "Vebus"
    assert server.pretty_alarm_name("HighCellVoltage") == "High Cell Voltage"
    assert server.pretty_alarm_name("high_cell_voltage") == "High Cell Voltage"


async def test_payload_includes_notifications():
    from inverter_dashboard import websocket_handler as wsh

    ms = server.MqttState()
    await ms.on_message(
        "inverter/notifications",
        json.dumps({"id": "n9", "level": "info", "title": "hello", "body": ""}).encode(),
    )
    wsh._state["mqtt_state"] = ms
    payload = wsh.build_payload()
    assert payload["notifications"][0]["title"] == "hello"
    wsh._state["mqtt_state"] = None
