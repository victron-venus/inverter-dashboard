"""Tests for rich HA entity display builders (ha_client.build_filtered_displays)."""

from inverter_dashboard.ha_client import _filtered_skeleton, build_filtered_displays


def _doc(entity_id, state, **attrs):
    d = {"entity_id": entity_id, "state": state, "attributes": dict(attrs)}
    if "friendly_name" not in attrs:
        d["attributes"]["friendly_name"] = entity_id.split(".")[-1].replace("_", " ").title()
    return d


class TestBuildFilteredDisplays:
    """Tests for the ha_filtered display builder."""

    def test_empty_everything(self):
        out = build_filtered_displays({}, {})
        assert out == _filtered_skeleton()

    def test_missing_docs_skipped(self):
        cfg = {"sensors": ["sensor.a"], "covers": ["cover.b"], "weather": "weather.c"}
        out = build_filtered_displays({"sensor.a": None, "cover.b": None, "weather.c": None}, cfg)
        assert not out["sensors"] and not out["covers"] and out["weather"] is None

    def test_sensor_display(self):
        docs = {"sensor.t": _doc("sensor.t", "21.5", unit_of_measurement="°C")}
        out = build_filtered_displays(docs, {"sensors": ["sensor.t"]})
        assert out["sensors"] == [
            {"entity_id": "sensor.t", "name": "T", "state": "21.5", "unit": "°C"}
        ]

    def test_number_display_with_range(self):
        docs = {"number.i": _doc("number.i", "12", min=6, max=32, step=2, unit_of_measurement="A")}
        out = build_filtered_displays(docs, {"numbers": ["number.i"]})
        n = out["numbers"][0]
        assert (n["value"], n["min"], n["max"], n["step"], n["unit"]) == (12.0, 6, 32, 2, "A")

    def test_number_unavailable_skipped(self):
        docs = {"number.i": _doc("number.i", "unavailable")}
        out = build_filtered_displays(docs, {"numbers": ["number.i"]})
        assert not out["numbers"]

    def test_cover_position_attr(self):
        docs = {"cover.b": _doc("cover.b", "open", current_position=42)}
        out = build_filtered_displays(docs, {"covers": ["cover.b"]})
        assert out["covers"] == [{"entity_id": "cover.b", "name": "B", "position": 42}]

    def test_cover_open_without_position(self):
        docs = {"cover.b": _doc("cover.b", "open")}
        out = build_filtered_displays(docs, {"covers": ["cover.b"]})
        assert out["covers"][0]["position"] == 100
        docs = {"cover.b": _doc("cover.b", "closed")}
        out = build_filtered_displays(docs, {"covers": ["cover.b"]})
        assert out["covers"][0]["position"] == 0

    def test_media_player_and_scene(self):
        docs = {
            "media_player.s": _doc("media_player.s", "playing"),
            "scene.m": _doc("scene.m", "scening"),
        }
        out = build_filtered_displays(
            docs, {"media_players": ["media_player.s"], "scenes": ["scene.m"]}
        )
        assert out["media_players"] == [
            {"entity_id": "media_player.s", "name": "S", "state": "playing"}
        ]
        assert out["scenes"] == [{"entity_id": "scene.m", "name": "M"}]

    def test_weather_with_forecast(self):
        fc = [{"datetime": "2026-08-24", "temperature": 25}]
        docs = {
            "weather.h": _doc(
                "weather.h",
                "sunny",
                temperature=24.5,
                temperature_unit="°C",
                forecast=fc,
            )
        }
        out = build_filtered_displays(docs, {"weather": "weather.h"})
        assert out["weather"] == {
            "entity_id": "weather.h",
            "name": "H",
            "state": "sunny",
            "temperature": 24.5,
            "unit": "°C",
            "forecast": fc,
        }

    def test_duplicate_entities_not_fetched_twice_is_caller_concern(self):
        """build_filtered_displays maps by id; same doc can feed multiple sections."""
        docs = {"sensor.a": _doc("sensor.a", "1", unit_of_measurement="W")}
        out = build_filtered_displays(docs, {"sensors": ["sensor.a"], "scenes": []})
        assert len(out["sensors"]) == 1
