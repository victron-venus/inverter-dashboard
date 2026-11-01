"""Malformed native leaves cannot revoke alarms or poison a later session."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from inverter_dashboard import config, gateway, server


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", "site")
    return server.MqttState()


@pytest.mark.parametrize(
    "path",
    [
        "0/Notifications/99/Description",
        "0/Notifications/-1/Description",
        "/Notifications/1/Description",
        "0/Notifications/1/Unknown",
        "0/Notifications/1/Description/extra",
    ],
)
async def test_unknown_platform_path_cannot_suppress_raw_alarms(state, path):
    await state.on_message(f"N/site/platform/{path}", b'{"value":"ignored"}')
    assert not state._platform_seen
    assert state._platform_slots == {}
    await state.on_message("N/site/battery/1/Alarms/HighVoltage", b'{"value":2}')
    assert len(state.get_notifications()) == 1


@pytest.mark.parametrize("payload", [b"{}", b"[]", b'{"value":{}}', b'{"value":true}'])
async def test_invalid_platform_description_does_not_establish_authority(state, payload):
    await state.on_message("N/site/platform/0/Notifications/1/Description", payload)
    assert not state._platform_seen
    assert state._platform_slots == {}


@pytest.mark.parametrize(
    "platform",
    [
        {"0/DeviceInstance": 0},
        {"0/Notifications/99/Description": "ignored"},
        {"0/Notifications/1/Unknown": 1},
        {"0/Notifications/1/Description": {"value": {}}},
        {"0/Notifications/1/DateTime": {"value": {}}},
        {"0/Notifications/1/DateTime": {"value": []}},
    ],
)
def test_irrelevant_platform_snapshot_preserves_alarm_fallback(state, platform):
    gateway.apply_snapshot(state, {"platform": platform, "battery": {"1/Alarms/HighVoltage": 2}})
    assert not state._platform_seen
    assert len(state.get_notifications()) == 1


@pytest.mark.parametrize("transport", ["mqtt", "igw"])
async def test_platform_authority_replaces_raw_banners_only(state, transport):
    state.push_notification({"id": "external", "title": "Controller warning"})
    if transport == "mqtt":
        await state.on_message("N/site/battery/1/Alarms/HighVoltage", b'{"value":2}')
        await state.on_message(
            "N/site/platform/0/Notifications/1/Description", b'{"value":"High voltage"}'
        )
    else:
        gateway.apply_snapshot(state, {"battery": {"1/Alarms/HighVoltage": 2}})
        gateway.apply_snapshot(
            state, {"platform": {"0/Notifications/1/Description": "High voltage"}}
        )
    assert {item["id"] for item in state.get_notifications()} == {
        "external",
        "victron-platform-0-1",
    }


def test_removed_snapshot_alarm_is_removed_without_touching_external_history(state):
    state.push_notification({"id": "external", "title": "Controller warning"})
    gateway.apply_snapshot(state, {"battery": {"1/Alarms/HighVoltage": 2}})
    gateway.apply_snapshot(state, {"battery": {}})
    assert [item["id"] for item in state.get_notifications()] == ["external"]
    assert state._alarm_values == {}


@pytest.mark.parametrize("invalid", [[], ["bad"], "bad", "", 0, False, None])
@pytest.mark.parametrize("bucket,other", [("battery", "vebus"), ("vebus", "battery")])
def test_malformed_snapshot_bucket_preserves_its_alarms_and_updates_other_bucket(
    state, invalid, bucket, other
):
    gateway.apply_snapshot(state, {bucket: {"1/Alarms/HighVoltage": 2}})
    gateway.apply_snapshot(state, {bucket: invalid, other: {"2/Alarms/LowVoltage": 1}})
    assert {item["id"]: item["level"] for item in state.get_notifications()} == {
        f"victron-alarm-{bucket}_1-HighVoltage": "alarm",
        f"victron-alarm-{other}_2-LowVoltage": "warning",
    }
    assert state._alarm_values[f"igw/{bucket}/1/Alarms/HighVoltage"] == 2
    # A valid empty bucket or absence from a complete snapshot does clear it.
    gateway.apply_snapshot(state, {bucket: {}})
    assert state.get_notifications() == []
    assert state._alarm_values == {}


def _existing_platform_event(state, dismissed):
    leaves = {
        "0/Notifications/1/Description": "Voltage alarm",
        "0/Notifications/1/DeviceName": "Battery",
        "0/Notifications/1/Type": 1,
        "0/Notifications/1/DateTime": 1000,
        "0/Notifications/1/Acknowledged": False,
    }
    gateway.apply_snapshot(state, {"platform": leaves, "system": {"0/Dc/Battery/Voltage": 51.0}})
    if dismissed:
        state.dismiss_notification("victron-platform-0-1")
    return leaves


@pytest.mark.parametrize("invalid", [[], ["bad"], "bad", "", 0, False, None])
@pytest.mark.parametrize("dismissed", [False, True])
def test_malformed_platform_bucket_preserves_event_and_other_telemetry(state, invalid, dismissed):
    leaves = _existing_platform_event(state, dismissed)
    slots = deepcopy(state._platform_slots)
    banners = deepcopy(state.get_notifications())

    gateway.apply_snapshot(state, {"platform": invalid, "system": {"0/Dc/Battery/Voltage": 52.1}})

    assert state._platform_slots == slots
    assert state.get_notifications() == banners
    assert state.get_state()["battery_voltage"] == 52.1
    # A later valid refresh of the same event must keep a local dismissal.
    gateway.apply_snapshot(state, {"platform": leaves})
    assert state.get_notifications() == banners
    # A genuinely new event still clears that dismissal.
    leaves["0/Notifications/1/DateTime"] = 2000
    gateway.apply_snapshot(state, {"platform": leaves})
    assert state.get_notifications()[0]["ts"] == "1970-01-01T00:33:20+00:00"


@pytest.mark.parametrize("snapshot", [{"platform": {}}, {}])
@pytest.mark.parametrize("dismissed", [False, True])
def test_empty_or_omitted_platform_bucket_still_removes_event(state, snapshot, dismissed):
    _existing_platform_event(state, dismissed)

    gateway.apply_snapshot(state, snapshot)

    assert state._platform_slots == {}
    assert state.get_notifications() == []


@pytest.mark.parametrize("invalid", [True, False, 1.5, "1.5", float("inf"), "NaN", {}])
@pytest.mark.parametrize("transport", ["mqtt", "igw"])
async def test_invalid_alarm_preserves_previous_value_and_other_alarms(state, invalid, transport):
    if transport == "mqtt":
        first = "N/site/battery/1/Alarms/HighVoltage"
        await state.on_message(first, b'{"value":2}')
        await state.on_message(first, json.dumps({"value": invalid}).encode())
        await state.on_message("N/site/battery/2/Alarms/LowVoltage", b'{"value":1}')
    else:
        gateway.apply_snapshot(state, {"battery": {"1/Alarms/HighVoltage": 2}})
        gateway.apply_snapshot(
            state,
            {
                "battery": {
                    "1/Alarms/HighVoltage": invalid,
                    "2/Alarms/LowVoltage": 1,
                }
            },
        )
    assert [item["level"] for item in state.get_notifications()] == ["alarm", "warning"]


def test_mqtt_session_reset_drops_previous_slot_fields_and_preserves_dismissal(state):
    base = "N/site/platform/0/Notifications/1"
    for field, value in {
        "Description": "Old alarm",
        "DateTime": 1000,
        "Acknowledged": True,
    }.items():
        state.handle_platform_notification(f"{base}/{field}", json.dumps({"value": value}).encode())
    state.clear_native_notifications()
    assert not state._platform_seen
    state.handle_platform_notification(f"{base}/Description", b'{"value":"New alarm"}')
    assert state.get_notifications()[0]["ts"] == ""
    state.handle_platform_notification(f"{base}/DateTime", b'{"value":2000}')
    state.dismiss_notification("victron-platform-0-1")
    state.clear_native_notifications()
    state.handle_platform_notification(f"{base}/Description", b'{"value":"New alarm"}')
    state.handle_platform_notification(f"{base}/DateTime", b'{"value":2000}')
    assert state.get_notifications() == []
    state.handle_platform_notification(f"{base}/DateTime", b'{"value":3000}')
    assert len(state.get_notifications()) == 1


@pytest.mark.parametrize("payload", [b"{}", b"[]", b"not JSON"])
async def test_malformed_alarm_envelope_does_not_clear_a_known_alarm(state, payload):
    topic = "N/site/battery/1/Alarms/HighVoltage"
    await state.on_message(topic, b'{"value":2}')
    await state.on_message(topic, payload)
    assert len(state.get_notifications()) == 1


@pytest.mark.parametrize("payload", [b"", b'{"value":null}', b'{"value":0}'])
async def test_alarm_tombstone_clears_a_known_alarm(state, payload):
    topic = "N/site/battery/1/Alarms/HighVoltage"
    await state.on_message(topic, b'{"value":2}')
    await state.on_message(topic, payload)
    assert state.get_notifications() == []


@pytest.mark.parametrize("value", [[], {}])
async def test_container_datetime_cannot_establish_platform_authority(state, value):
    await state.on_message(
        "N/site/platform/0/Notifications/1/DateTime", json.dumps({"value": value}).encode()
    )
    assert not state._platform_seen
    assert state._platform_slots == {}


@pytest.mark.parametrize("value", [[], {}])
async def test_malformed_mqtt_datetime_cannot_mutate_an_accepted_event(state, value):
    base = "N/site/platform/0/Notifications/1"
    await state.on_message(f"{base}/Description", b'{"value":"Voltage alarm"}')
    await state.on_message(f"{base}/DateTime", b'{"value":1000}')
    before = state.get_notifications()[0].copy()
    await state.on_message(f"{base}/DateTime", json.dumps({"value": value}).encode())
    assert state.get_notifications()[0] == before


@pytest.mark.parametrize("value", [[], {}])
def test_invalid_datetime_in_complete_snapshot_has_no_inherited_time(state, value):
    leaves = {"0/Notifications/1/Description": "Voltage alarm", "0/Notifications/1/DateTime": 1000}
    gateway.apply_snapshot(state, {"platform": leaves})
    leaves["0/Notifications/1/DateTime"] = {"value": value}
    gateway.apply_snapshot(state, {"platform": leaves})
    assert state.get_notifications()[0]["title"] == "Voltage alarm"
    assert state.get_notifications()[0]["ts"] == ""


@pytest.mark.parametrize("field", ["DateTime", "Description"])
async def test_platform_leaf_tombstone_only_clears_its_own_field(state, field):
    base = "N/site/platform/0/Notifications/1"
    await state.on_message(f"{base}/Description", b'{"value":"Voltage alarm"}')
    await state.on_message(f"{base}/DateTime", b'{"value":1000}')
    await state.on_message(f"{base}/DeviceName", b'{"value":"Battery"}')
    await state.on_message(f"{base}/{field}", b"")
    slot = state._platform_slots[("0", 1)]
    assert slot["device_name"] == "Battery"
    if field == "DateTime":
        assert slot["description"] == "Voltage alarm"
        assert state.get_notifications()[0]["ts"] == ""
    else:
        assert slot["date_time"] == 1000
        assert state.get_notifications() == []


@pytest.mark.parametrize("boundary", ["subscribe", "disconnect"])
async def test_actual_session_boundary_retires_native_fields(state, monkeypatch, boundary):
    client = object()
    owner = SimpleNamespace(mqtt_connected=True, mqtt_client=client)
    monkeypatch.setattr(server, "_push_service", None)
    subscribe = AsyncMock()
    monkeypatch.setattr(server, "_subscribe_topics", subscribe)
    state.push_notification({"id": "external", "title": "Controller warning"})
    state.handle_alarm("N/site/battery/1/Alarms/HighVoltage", b'{"value":2}')
    base = "N/site/platform/0/Notifications/1"
    state.handle_platform_notification(f"{base}/Description", b'{"value":"Old alarm"}')
    state.handle_platform_notification(f"{base}/DateTime", b'{"value":1000}')
    state.handle_platform_notification(f"{base}/Acknowledged", b'{"value":true}')
    if boundary == "subscribe":
        await server._prepare_mqtt_session(owner, state, lambda: True, client)
        subscribe.assert_awaited_once()
    else:
        await server._finish_mqtt_session(owner, state, lambda: True, client, None)
        assert not owner.mqtt_connected
    assert not state._platform_seen
    assert state._platform_slots == {}
    assert state._alarm_values == {}
    assert [item["id"] for item in state.get_notifications()] == ["external"]
    state.handle_platform_notification(f"{base}/Description", b'{"value":"New alarm"}')
    assert state.get_notifications()[-1]["title"] == "New alarm"
    assert state.get_notifications()[-1]["ts"] == ""
