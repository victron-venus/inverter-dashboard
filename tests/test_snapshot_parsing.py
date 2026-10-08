"""Snapshot parsing preserves wire precision and native validation order."""

import json

import httpx
import pytest

from inverter_dashboard import cerbo, config, gateway


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "timestamp", ["1735689600.0000001", "1735689600", '"1735689600"', "null", "true"]
)
async def test_snapshot_reparses_only_fractional_notification_time(monkeypatch, wrapped, timestamp):
    monkeypatch.setattr(config, "GATEWAY_URL", "https://gateway.example")
    monkeypatch.setattr(gateway, "build_headers", dict)
    raw_value = '{"value":' + timestamp + ',"extra":3.5}' if wrapped else timestamp
    body = (
        '{"platform":{"0/Notifications/1/DateTime":'
        + raw_value
        + ',"0/Notifications/1/Other":4.5},"system":{"0/Dc/Battery/Voltage":52.1}}'
    )
    response = httpx.Response(200, content=body)
    parse_calls = []
    original_json = response.json

    def tracked_json(**kwargs):
        parse_calls.append(kwargs)
        return original_json(**kwargs)

    monkeypatch.setattr(response, "json", tracked_json)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: response)
    ) as client:
        snapshot = await gateway.fetch_snapshot(client)

    value = snapshot["platform"]["0/Notifications/1/DateTime"]
    if wrapped:
        assert value["extra"] == 3.5
        value = value["value"]
    expected = timestamp if "." in timestamp else json.loads(timestamp)
    assert value == expected
    assert type(value) is type(expected)
    assert snapshot["platform"]["0/Notifications/1/Other"] == 4.5
    assert snapshot["system"]["0/Dc/Battery/Voltage"] == 52.1
    assert parse_calls == ([{}, {"parse_float": str}] if "." in timestamp else [{}])


class TrackingOverlay(cerbo.CerboOverlayMixin):
    """Observe subclass validators and lifecycle calls without UI transforms."""

    def __init__(self):
        self._init_cerbo()
        self.calls = []

    def _valid_path_value(self, kind, path, value):
        self.calls.append(("valid", kind, path, value))
        return path != "Rejected"

    def _known_path(self, kind, path):
        self.calls.append(("known", kind, path))
        return path != "Unknown"

    def _claim_invalid_leaf(self, kind, instance, path):
        self.calls.append(("invalid", kind, instance, path))

    def _note_native_observation(self, source):
        self.calls.append(("observed", source))

    def _apply_cerbo_overlays(self):
        self.calls.append(("overlays",))


def test_snapshot_validation_keeps_subclass_order_and_removals(monkeypatch):
    state = TrackingOverlay()
    state._cerbo_devices = {"battery": {"old": {"Soc": 99}}}
    state._water_mode_observations = {"old": 42}
    monkeypatch.setattr(cerbo.time, "monotonic", lambda: 100.0)
    state.replace_cerbo_snapshot(
        {
            "system": [],
            "battery": {
                7: 1,
                "bad": 1,
                "/Soc": 1,
                "0/": 1,
                "0/Rejected": 1,
                "0/Unknown": 1,
                "0/Custom/Path": 3,
                "0/Soc": None,
            },
            "pump": {"2/Mode": 2, "3/Mode": True},
        }
    )
    assert state._cerbo_devices == {
        "battery": {"0": {"Custom/Path": 3, "Soc": None}},
        "pump": {"2": {"Mode": 2}, "3": {"Mode": True}},
    }
    assert state._water_mode_observations == {"2": 100.0}
    assert state.calls == [
        ("valid", "battery", "Rejected", 1),
        ("valid", "battery", "Unknown", 1),
        ("known", "battery", "Unknown"),
        ("valid", "battery", "Custom/Path", 3),
        ("known", "battery", "Custom/Path"),
        ("valid", "battery", "Soc", None),
        ("known", "battery", "Soc"),
        ("invalid", "battery", "0", "Soc"),
        ("valid", "pump", "Mode", 2),
        ("known", "pump", "Mode"),
        ("valid", "pump", "Mode", True),
        ("known", "pump", "Mode"),
        ("observed", "igw"),
        ("overlays",),
    ]
