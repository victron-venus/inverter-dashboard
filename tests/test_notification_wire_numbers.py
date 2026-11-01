"""Notification enum validation must see exact wire numbers, not rounded floats."""

import json

import httpx
import pytest

from inverter_dashboard import config, gateway, server


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", "site")
    monkeypatch.setattr(config, "GATEWAY_URL", "https://gateway.example")
    monkeypatch.setattr(gateway, "build_headers", dict)
    return server.MqttState()


async def send_number(state, transport, bucket, leaf, token):
    if transport == "mqtt":
        await state.on_message(f"N/site/{bucket}/{leaf}", ('{"value":' + token + "}").encode())
        return
    raw = '{"value":' + token + "}" if transport == "igw-wrapped" else token
    response = (
        '{"' + bucket + '":{"' + leaf + '":' + raw + '},"system":{"0/Dc/Battery/Voltage":51.25}}'
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response))
    ) as client:
        snapshot = await gateway.fetch_snapshot(client)
    assert type(snapshot["system"]["0/Dc/Battery/Voltage"]) is float
    json.dumps(snapshot)
    gateway.apply_snapshot(state, snapshot)


@pytest.mark.parametrize("transport", ["mqtt", "igw-flat", "igw-wrapped"])
@pytest.mark.parametrize("token", ["1.0000000000000000001", "1e-10000"])
async def test_near_enum_number_cannot_change_a_live_alarm(state, transport, token):
    await send_number(state, transport, "battery", "1/Alarms/HighVoltage", "2")
    await send_number(state, transport, "battery", "1/Alarms/HighVoltage", token)
    assert [item["level"] for item in state.get_notifications()] == ["alarm"]


@pytest.mark.parametrize("transport", ["mqtt", "igw-flat", "igw-wrapped"])
@pytest.mark.parametrize("field", ["Type", "Active", "Acknowledged", "Silenced"])
@pytest.mark.parametrize("token", ["1.0000000000000000001", "1e-10000"])
async def test_near_enum_platform_number_does_not_establish_authority(
    state, transport, field, token
):
    await send_number(state, transport, "platform", f"0/Notifications/1/{field}", token)
    assert not state._platform_seen
    assert state._platform_slots == {}


@pytest.mark.parametrize("transport", ["mqtt", "igw-flat", "igw-wrapped"])
@pytest.mark.parametrize("token", ["1.0", "1e0"])
@pytest.mark.parametrize("field", ["Type", "Active"])
async def test_exact_enum_numeric_encodings_keep_their_original_meaning(
    state, transport, field, token
):
    await send_number(state, transport, "platform", f"0/Notifications/1/{field}", token)
    assert state._platform_seen
    slot = state._platform_slots[("0", 1)]
    if field == "Type":
        assert slot["notif_type"] == 1
        assert slot["push_type_known"]
    else:
        assert slot["active"] is True


@pytest.mark.parametrize("field", ["Description", "DeviceName", "Service"])
@pytest.mark.parametrize("token", ["1.5", "1e-10000"])
async def test_numeric_text_fields_do_not_hide_mqtt_raw_alarm(state, field, token):
    await send_number(state, "mqtt", "battery", "1/Alarms/HighVoltage", "2")
    original = state.get_notifications()

    await send_number(state, "mqtt", "platform", f"0/Notifications/1/{field}", token)

    assert not state._platform_seen
    assert state._platform_slots == {}
    assert state.get_notifications() == original
