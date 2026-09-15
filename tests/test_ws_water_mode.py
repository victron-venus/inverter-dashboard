"""Native water controls use only the selected pump and current LAN client."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.inverter_dashboard import config, gateway, ha_client, websocket_handler
from src.inverter_dashboard.server import MqttState


@pytest.fixture
def water_context(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", "site-a")
    monkeypatch.setattr(config, "WATER_PUMP_INSTANCE", 7)
    monkeypatch.setattr(config, "WATER_VALVE_INSTANCE", 9)
    monkeypatch.setattr(gateway, "prefer_gateway", lambda: False)
    monkeypatch.setattr(ha_client, "merge_overlay", lambda value: value)
    state = MqttState()
    for instance in (7, 9):
        state._handle_cerbo_device(f"N/site-a/pump/{instance}/Mode", b'{"value":0}')
    client = SimpleNamespace(publish=AsyncMock())
    app = SimpleNamespace(
        mqtt_client=client, mqtt_state=state, mqtt_connected=True, data_source="mqtt"
    )
    monkeypatch.setitem(websocket_handler._state, "app_state", app)
    monkeypatch.setitem(websocket_handler._state, "mqtt_state", state)
    app.mqtt_publish = AsyncMock()
    monkeypatch.setattr(websocket_handler, "mqtt_publish", app.mqtt_publish)
    return app


@pytest.mark.parametrize("which,instance", [("pump", 7), ("valve", 9)])
@pytest.mark.parametrize("mode", [0, 1, 2])
async def test_water_mode_publishes_native_configured_target(water_context, which, instance, mode):
    before = copy.deepcopy(water_context.mqtt_state.get_state())
    await websocket_handler._dispatch_action(
        "water_mode", {"which": which, "mode": mode}, water_context.mqtt_client
    )
    args, kwargs = water_context.mqtt_client.publish.call_args
    assert args[0] == f"W/site-a/pump/{instance}/Mode"
    assert json.loads(args[1]) == {"value": mode}
    assert kwargs == {"qos": 0, "retain": False}
    assert water_context.mqtt_state.get_state() == before
    water_context.mqtt_publish.assert_not_called()
    assert websocket_handler.build_payload()["water_controls_available"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"which": "other", "mode": 1},
        {"which": "pump", "mode": -1},
        {"which": "pump", "mode": 3},
        {"which": "pump", "mode": 0.5},
        {"which": "pump", "mode": "1"},
        {"which": "pump", "mode": True},
        {"which": "pump", "mode": float("nan")},
        {"which": "pump"},
        {"mode": 1},
    ],
)
async def test_water_mode_rejects_invalid_enums(water_context, payload):
    with pytest.raises(ValueError):
        await websocket_handler._dispatch_action("water_mode", payload, water_context.mqtt_client)
    water_context.mqtt_client.publish.assert_not_called()


@pytest.mark.parametrize(
    "condition", ["offline", "gateway", "missing_portal", "bad_portal", "stale_client"]
)
async def test_water_mode_requires_current_direct_connection(water_context, monkeypatch, condition):
    client = water_context.mqtt_client
    if condition == "offline":
        water_context.mqtt_connected = False
    elif condition == "gateway":
        water_context.data_source = "igw"
        monkeypatch.setattr(gateway, "prefer_gateway", lambda: True)
    elif condition == "missing_portal":
        water_context.mqtt_state._portal_id = ""
    elif condition == "bad_portal":
        water_context.mqtt_state._portal_id = "site/other"
    else:
        client = SimpleNamespace(publish=AsyncMock())
    with pytest.raises(RuntimeError):
        await websocket_handler._dispatch_action("water_mode", {"which": "pump", "mode": 1}, client)
    client.publish.assert_not_called()
    water_context.mqtt_client.publish.assert_not_called()
    if condition != "stale_client":
        assert websocket_handler.build_payload()["water_controls_available"] is False


@pytest.mark.parametrize("payload", [None, b'{"value":null}', b'{"value":4}', b'{"value":1.5}'])
async def test_water_mode_requires_known_mode_of_exact_device(water_context, payload):
    water_context.mqtt_state._handle_cerbo_device("N/site-a/pump/7", b"")
    if payload is not None:
        water_context.mqtt_state._handle_cerbo_device("N/site-a/pump/7/Mode", payload)
    with pytest.raises(RuntimeError):
        await websocket_handler._dispatch_action(
            "water_mode", {"which": "pump", "mode": 1}, water_context.mqtt_client
        )
    water_context.mqtt_client.publish.assert_not_called()


async def test_water_mode_rejects_disconnected_native_device(water_context):
    water_context.mqtt_state._handle_cerbo_device("N/site-a/pump/7/Connected", b'{"value":0}')
    with pytest.raises(RuntimeError):
        await websocket_handler._dispatch_action(
            "water_mode", {"which": "pump", "mode": 1}, water_context.mqtt_client
        )
    water_context.mqtt_client.publish.assert_not_called()


async def test_water_publish_error_does_not_change_state_or_fall_back(water_context):
    before = copy.deepcopy(water_context.mqtt_state.get_state())
    water_context.mqtt_client.publish.side_effect = RuntimeError("connection lost")
    with pytest.raises(RuntimeError, match="connection lost"):
        await websocket_handler._dispatch_action(
            "water_mode", {"which": "valve", "mode": 1}, water_context.mqtt_client
        )
    assert water_context.mqtt_state.get_state() == before
    water_context.mqtt_publish.assert_not_called()
