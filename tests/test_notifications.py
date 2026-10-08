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

    # Warning transition upserts same id (desktop banner dedupe)
    await ms.on_message(topic, b'{"value": 1}')
    assert len(ms.get_notifications()) == 1
    assert ms.get_notifications()[0]["level"] == "warning"

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


async def test_platform_notification_mqtt():
    ms = server.MqttState()
    base = "N/portal/platform/0/Notifications/3"
    await ms.on_message(f"{base}/Description", b'{"value": "High cell voltage"}')
    await ms.on_message(f"{base}/DeviceName", b'{"value": "Bank"}')
    await ms.on_message(f"{base}/Type", b'{"value": 1}')
    await ms.on_message(f"{base}/Active", b'{"value": 1}')
    assert ms._platform_seen is True
    notifs = ms.get_notifications()
    assert len(notifs) == 1
    assert notifs[0]["id"] == "victron-platform-0-3"
    assert notifs[0]["title"] == "High cell voltage"
    assert notifs[0]["body"] == "Bank"
    assert notifs[0]["level"] == "alarm"
    assert notifs[0]["source"] == "victron"

    # Acknowledged hides banner
    await ms.on_message(f"{base}/Acknowledged", b'{"value": 1}')
    assert all(n["id"] != "victron-platform-0-3" for n in ms.get_notifications())


def test_igw_snapshot_platform_banners(ms=None):
    from inverter_dashboard import gateway

    ms = server.MqttState()
    snap = {
        "system": {},
        "battery": {},
        "solarcharger": {},
        "pvinverter": {},
        "vebus": {},
        "acload": {},
        "tank": {},
        "pump": {},
        "ev": {},
        "evcharger": {},
        "platform": {
            "0/Notifications/1/Description": "Grid lost",
            "0/Notifications/1/Type": 0,
            "0/Notifications/1/Active": True,
            "0/Notifications/1/DeviceName": "MultiPlus",
        },
    }
    gateway.apply_snapshot(ms, snap)
    notifs = ms.get_notifications()
    assert len(notifs) == 1
    assert notifs[0]["id"] == "victron-platform-0-1"
    assert notifs[0]["level"] == "warning"
    assert notifs[0]["body"] == "MultiPlus"

    ms.dismiss_notification("victron-platform-0-1")
    assert ms.get_notifications() == []
    # Re-apply same snapshot — user_dismissed sticky until Active clears
    gateway.apply_snapshot(ms, snap)
    assert ms.get_notifications() == []


def test_igw_alarm_fallback_when_no_platform():
    from inverter_dashboard import gateway

    ms = server.MqttState()
    snap = {
        "system": {},
        "battery": {"512/Alarms/HighVoltage": 2},
        "solarcharger": {},
        "pvinverter": {},
        "vebus": {},
        "acload": {},
        "tank": {},
        "pump": {},
        "ev": {},
        "evcharger": {},
        "platform": {},
    }
    gateway.apply_snapshot(ms, snap)
    notifs = ms.get_notifications()
    assert len(notifs) == 1
    assert notifs[0]["level"] == "alarm"
    assert "High Voltage" in notifs[0]["body"]


def test_snapshot_alarm_updates_keep_bucket_order_and_continue_after_first_change():
    from inverter_dashboard import notifications

    ms = server.MqttState()
    calls = []

    def pretty_service(value):
        calls.append(("service", value))
        return value

    def pretty_alarm(value):
        calls.append(("alarm", value))
        return value

    snapshot = {
        "battery": {"1/Alarms/High": {"value": 2}, "2/Alarms/Low": 1},
        "vebus": {"0/Alarms/Grid": 2},
    }
    assert notifications.mqtt_sync_alarms_from_snapshot(ms, snapshot, pretty_service, pretty_alarm)
    assert calls == [
        ("service", "battery_1"),
        ("alarm", "High"),
        ("service", "battery_2"),
        ("alarm", "Low"),
        ("service", "vebus_0"),
        ("alarm", "Grid"),
    ]
    assert [n["id"] for n in ms.notifications] == [
        "victron-alarm-battery_1-High",
        "victron-alarm-battery_2-Low",
        "victron-alarm-vebus_0-Grid",
    ]
    calls.clear()
    snapshot["battery"]["1/Alarms/High"] = {"value": 0}
    snapshot["vebus"]["0/Alarms/Grid"] = 1
    assert notifications.mqtt_sync_alarms_from_snapshot(ms, snapshot, pretty_service, pretty_alarm)
    assert calls == [("service", "vebus_0"), ("alarm", "Grid")]
    assert [n["id"] for n in ms.notifications] == [
        "victron-alarm-battery_2-Low",
        "victron-alarm-vebus_0-Grid",
    ]
    assert ms.notifications[-1]["level"] == "warning"


def test_platform_notifications_skip_alarm_snapshot_access():
    from inverter_dashboard import notifications

    class UnreadableSnapshot(dict):
        def get(self, *_args):
            raise AssertionError("Platform notifications must bypass alarm fallback")

    ms = server.MqttState()
    ms._platform_seen = True
    snapshot = UnreadableSnapshot()
    assert not notifications.mqtt_sync_alarms_from_snapshot(ms, snapshot, str, str)
    assert ms._alarm_values == {}


def test_alarm_callback_failure_preserves_value_update_order():
    import pytest

    from inverter_dashboard import notifications

    ms = server.MqttState()
    snapshot = {"battery": {"1/Alarms/High": 2}}
    failure = RuntimeError("name lookup failed")

    def fail_name(_value):
        raise failure

    with pytest.raises(RuntimeError, match="name lookup failed") as caught:
        notifications.mqtt_sync_alarms_from_snapshot(ms, snapshot, fail_name, str)
    assert caught.value is failure
    assert ms._alarm_values == {"igw/battery/1/Alarms/High": 2}
    assert ms.notifications == []


def test_alarm_numeric_overflow_preserves_previous_leaf_value():
    import pytest

    from inverter_dashboard import notifications

    ms = server.MqttState()
    ms._alarm_values["igw/battery/1/Alarms/High"] = 1
    snapshot = {"battery": {"1/Alarms/High": float("inf")}}
    with pytest.raises(OverflowError):
        notifications.mqtt_sync_alarms_from_snapshot(ms, snapshot, str, str)
    assert ms._alarm_values == {"igw/battery/1/Alarms/High": 1}
    assert ms.notifications == []
