"""Tests for ha_client pure functions (no network, no HA required)."""

from src.inverter_dashboard.ha_client import (
    _appliance_fallback,
    _appliance_field_value,
    _boolish,
    _default_switch_label,
    _parse_ha_switch_entities,
    _sensor_state_to_seconds,
)


# ---------------------------------------------------------------------------
# _sensor_state_to_seconds
# ---------------------------------------------------------------------------
class TestSensorStateToSeconds:
    """Tests for _sensor_state_to_seconds."""
    def test_none_returns_zero(self):
        assert _sensor_state_to_seconds(None) == 0

    def test_empty_string(self):
        assert _sensor_state_to_seconds("") == 0

    def test_unavailable(self):
        assert _sensor_state_to_seconds("unavailable") == 0

    def test_unknown(self):
        assert _sensor_state_to_seconds("unknown") == 0

    def test_none_string(self):
        assert _sensor_state_to_seconds("None") == 0

    def test_integer_string(self):
        assert _sensor_state_to_seconds("120") == 120

    def test_float_string(self):
        assert _sensor_state_to_seconds("90.5") == 90

    def test_hh_mm_ss(self):
        assert _sensor_state_to_seconds("1:23:45") == 1 * 3600 + 23 * 60 + 45

    def test_hh_mm_ss_zero(self):
        assert _sensor_state_to_seconds("0:00:00") == 0

    def test_mm_ss(self):
        assert _sensor_state_to_seconds("5:30") == 5 * 60 + 30

    def test_invalid_format(self):
        assert _sensor_state_to_seconds("not-a-time") == 0

    def test_single_number(self):
        assert _sensor_state_to_seconds("300") == 300


# ---------------------------------------------------------------------------
# _boolish
# ---------------------------------------------------------------------------
class TestBoolish:
    """Tests for _boolish."""
    def test_none_is_false(self):
        assert _boolish(None) is False

    def test_on(self):
        assert _boolish("on") is True

    def test_true(self):
        assert _boolish("true") is True

    def test_yes(self):
        assert _boolish("yes") is True

    def test_one(self):
        assert _boolish("1") is True

    def test_off(self):
        assert _boolish("off") is False

    def test_false(self):
        assert _boolish("false") is False

    def test_case_insensitive(self):
        assert _boolish("ON") is True
        assert _boolish("True") is True

    def test_empty_string(self):
        assert _boolish("") is False

    def test_random_string(self):
        assert _boolish("something") is False


# ---------------------------------------------------------------------------
# _parse_ha_switch_entities
# ---------------------------------------------------------------------------
class TestParseSwitchEntities:
    """Tests for _parse_ha_switch_entities."""
    def test_none_input(self):
        entities, labels = _parse_ha_switch_entities(None)
        assert not entities
        assert not labels

    def test_empty_dict(self):
        entities, labels = _parse_ha_switch_entities({})
        assert not entities
        assert not labels

    def test_string_value(self):
        raw = {"recliner": "switch.recliner_recliner"}
        entities, labels = _parse_ha_switch_entities(raw)
        assert entities == {"recliner": "switch.recliner_recliner"}
        assert not labels

    def test_tuple_value(self):
        raw = {"recliner": ("switch.recliner_recliner", "Recliner")}
        entities, labels = _parse_ha_switch_entities(raw)
        assert entities == {"recliner": "switch.recliner_recliner"}
        assert labels == {"recliner": "Recliner"}

    def test_dict_value(self):
        raw = {"garage": {"entity": "switch.garage", "label": "Garage light"}}
        entities, labels = _parse_ha_switch_entities(raw)
        assert entities == {"garage": "switch.garage"}
        assert labels == {"garage": "Garage light"}

    def test_dict_value_alt_keys(self):
        raw = {"light": {"id": "light.kitchen", "name": "Kitchen"}}
        entities, labels = _parse_ha_switch_entities(raw)
        assert entities == {"light": "light.kitchen"}
        assert labels == {"light": "Kitchen"}

    def test_empty_key_skipped(self):
        raw = {"": "switch.foo"}
        entities, labels = _parse_ha_switch_entities(raw)
        assert not entities
        assert not labels

    def test_whitespace_stripped(self):
        raw = {"key": "  switch.foo  "}
        entities, _ = _parse_ha_switch_entities(raw)
        assert entities == {"key": "switch.foo"}

    def test_mixed_values(self):
        raw = {
            "a": "switch.a",
            "b": ("switch.b", "B label"),
            "c": {"entity": "switch.c", "label": "C label"},
        }
        entities, labels = _parse_ha_switch_entities(raw)
        assert set(entities.keys()) == {"a", "b", "c"}
        assert labels["b"] == "B label"
        assert labels["c"] == "C label"

    def test_non_dict_input(self):
        entities, labels = _parse_ha_switch_entities("not a dict")
        assert not entities
        assert not labels


# ---------------------------------------------------------------------------
# _appliance_field_value
# ---------------------------------------------------------------------------
class TestApplianceFieldValue:
    """Tests for _appliance_field_value."""
    def test_binary_sensor_on(self):
        assert (
            _appliance_field_value("dishwasher_running", "binary_sensor.dishwasher_running", "on")
            is True
        )

    def test_binary_sensor_off(self):
        assert (
            _appliance_field_value("dishwasher_running", "binary_sensor.dishwasher_running", "off")
            is False
        )

    def test_sensor_seconds(self):
        assert (
            _appliance_field_value("dishwasher_duration", "sensor.dishwasher_duration", "3600")
            == 3600
        )

    def test_sensor_hh_mm_ss(self):
        assert (
            _appliance_field_value("washer_time", "sensor.washer_remaining_time", "1:30:00") == 5400
        )

    def test_sensor_power_running(self):
        assert _appliance_field_value("washer_power", "sensor.washer_power", "150.0") is True

    def test_sensor_power_idle(self):
        assert _appliance_field_value("washer_power", "sensor.washer_power", "0.5") is False

    def test_sensor_power_none(self):
        assert _appliance_field_value("washer_power", "sensor.washer_power", None) is False

    def test_switch_on(self):
        assert _appliance_field_value("heater", "switch.heater_l", "on") is True

    def test_unknown_domain(self):
        assert _appliance_field_value("foo", "script.foo", "on") is False


# ---------------------------------------------------------------------------
# _appliance_fallback
# ---------------------------------------------------------------------------
class TestApplianceFallback:
    """Tests for _appliance_fallback."""
    def test_time_key(self):
        assert _appliance_fallback("washer_time") == 0

    def test_duration_key(self):
        assert _appliance_fallback("dishwasher_duration") == 0

    def test_other_key(self):
        assert _appliance_fallback("dishwasher_running") is False


# ---------------------------------------------------------------------------
# _default_switch_label
# ---------------------------------------------------------------------------
class TestDefaultSwitchLabel:
    """Tests for _default_switch_label."""
    def test_plain_key(self):
        assert _default_switch_label("recliner") == "RECLINER"

    def test_home_prefix_stripped(self):
        assert _default_switch_label("home_recliner") == "RECLINER"

    def test_underscore_to_space(self):
        assert _default_switch_label("garage_light") == "GARAGE LIGHT"
