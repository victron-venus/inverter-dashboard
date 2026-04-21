"""
Home Assistant credentials for inverter-dashboard (local copy — never commit).

Docker: copy this file to your host as config/ha_secrets.py and bind-mount that
folder to /app/config (env INVERTER_DASHBOARD_CONFIG=/app/config).
Local dev: run scripts/init-config.sh to create config/ha_secrets.py from this file,
or copy to ha_secrets.py next to server.py (legacy).

When HA_DIRECT_CONTROLS is True, the dashboard reads toggle/switch states and
sends toggle commands directly to Home Assistant instead of relying on Cerbo MQTT
state from inverter-control. That keeps MQTT traffic smaller and lets you change
entities here without touching inverter-control.
"""

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "REPLACE_WITH_LONG_LIVED_ACCESS_TOKEN"

# Master switch: use HA REST for switches/booleans below (recommended with MQTT_SLIM on Cerbo)
HA_DIRECT_CONTROLS = True

# Poll interval for HA states (seconds). Increase on slow hosts.
HA_POLL_INTERVAL_SEC = 12

# ---------------------------------------------------------------------------
# Entities — keys must match ui_config ids / state_keys used by the Vue app
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

# Home section buttons (switch.*)
HA_SWITCH_ENTITIES = {
    "home_recliner": "switch.recliner_recliner",
    "home_garage": "switch.garage_opener_l",
    "laundry_outlet": "switch.laundry_zigbee_switch",
}

# Water card (optional — leave empty strings to skip)
HA_WATER_VALVE_ENTITY = "switch.shutoff_valve"
HA_PUMP_SWITCH_ENTITY = "switch.pump_switch"
