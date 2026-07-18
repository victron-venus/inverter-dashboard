"""
Site Configuration Example

Copy this file to site_config.py and fill in your values.
site_config.py is NOT tracked by git.

Docker: place **site_config.py** on the host inside the folder you mount at **/app/config**
(set **INVERTER_DASHBOARD_CONFIG=/app/config** in compose — that path is the mount point, not a repo subfolder).
Local dev: run **scripts/init-config.sh** to create **./site_config.py** next to **server.py**, or copy this file manually.

When HA_DIRECT_CONTROLS is True, the dashboard reads toggle/switch states and
sends toggle commands directly to Home Assistant instead of relying on Cerbo MQTT
state from inverter-control. That keeps MQTT traffic smaller and lets you change
entities here without touching inverter-control.

When inverter-control uses MQTT_SLIM_STATE=True, dishwasher/washer/dryer fields are
not published on inverter/state — set HA_APPLIANCE_ENTITIES (below) so the dashboard
polls those sensors from HA REST (same entity IDs as in inverter-control site_config).
"""

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "REPLACE_WITH_LONG_LIVED_ACCESS_TOKEN"

# Master switch: use HA REST for switches/booleans below (recommended with MQTT_SLIM on Cerbo).
# When True, dashboard does not fall back to MQTT/Cerbo for those entity states if HA REST is down.
HA_DIRECT_CONTROLS = True

# Poll interval for HA states (seconds). Increase on slow hosts.
HA_POLL_INTERVAL_SEC = 12

# ---------------------------------------------------------------------------
# Entities — keys are WebSocket/MQTT state field names (booleans for switches/lights).
# Home card buttons are built automatically from HA_SWITCH_ENTITIES (+ optional HA_SWITCH_LABELS).
# ---------------------------------------------------------------------------
# Header mode toggles (input_boolean.*)
HA_BOOLEAN_ENTITIES = {
    "only_charging": "input_boolean.only_charging",
    "no_feed": "input_boolean.no_feed",
    "house_support": "input_boolean.house_support",
    "charge_battery": "input_boolean.charge_battery",
    "do_not_supply_charger": "input_boolean.do_not_supply_charger",
    "set_limit_to_ev_charger": "input_boolean.set_limit_to_ev_charger",
    "minimize_charging": "input_boolean.minimize_charging",
}

# Home section buttons — keys are WebSocket state keys (bool).
# Each value may be:
#   - entity id string: "switch.foo"
#   - (entity_id, "Short label") tuple
#   - dict: {"entity": "switch.foo", "label": "Short"}  (keys "short" / "name" also work for label)
# Supported domains include switch.* and light.* (toggle via HA REST when HA_DIRECT_CONTROLS).
HA_SWITCH_ENTITIES = {
    "home_recliner": ("switch.recliner_recliner", "Recliner"),
    "home_garage": ("switch.garage_opener_l", "Garage"),
    "laundry_outlet": ("switch.laundry_zigbee_switch", "Laundry"),
    "garage_light": {"entity": "light.garage", "label": "Garage light"},
}

# Optional extra overrides if you still use plain string values above (HA_SWITCH_LABELS wins over embedded labels).
HA_SWITCH_LABELS = {}

# Dishwasher/washer/dryer telemetry — same dashboard keys as inverter/state would carry without MQTT_SLIM.
# Optional washer_power/dryer_power entries: use switch.* or sensor.* (>1 W counts as running).
HA_APPLIANCE_ENTITIES = {
    "dishwasher_running": "binary_sensor.dishwasher_running",
    "dishwasher_duration": "sensor.dishwasher_duration",
    "washer_time": "sensor.washer_remaining_time",
    "dryer_time": "sensor.dryer_remaining_time",
    # "washer_power": "sensor.washer_power_estimate",
    # "dryer_power": "sensor.dryer_power_estimate",
}

# Water card (optional — leave empty strings to skip)
HA_WATER_VALVE_ENTITY = "switch.shutoff_valve"
HA_PUMP_SWITCH_ENTITY = "switch.pump_switch"
