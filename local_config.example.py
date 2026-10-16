"""Private Home Assistant configuration template.

Run scripts/init-config.sh to create ignored local_config.py, then edit that copy.
For containers, mount its directory at INVERTER_DASHBOARD_CONFIG (often /app/config).
This file does not configure native MQTT/IGW; those use environment variables.
No network requests are made by importing this template.
"""

# Supply your own HA endpoint and token privately. Empty defaults perform no HA I/O.
HA_URL = ""
HA_TOKEN = ""  # nosec B105 -- empty configuration, not an embedded credential
HA_DIRECT_CONTROLS = False
HA_POLL_INTERVAL_SEC = 12

# HA owns only configured HA fields. Native power, battery, Water, EV, controller
# flags and freshness/capability metadata cannot be overridden with HA mirrors.
# In particular, do not map only_charging/no_feed/etc. here; inverter-control owns
# those header flags. This mapping is for any additional HA-owned boolean fields.
HA_BOOLEAN_ENTITIES = {}

# Home buttons have no built-in personal entity list. Accepted values:
#   "light.example_workbench"
#   ("light.example_workbench", "Workbench")
#   {"entity": "light.example_workbench", "label": "Workbench",
#    "order": 10, "enabled": True}
# The example entity is fictitious; replace it with a real entity in your HA.
# Equal orders preserve declaration order. Disabled entries are not displayed,
# polled or permitted as toggle targets. Empty means no Home buttons.
HA_SWITCH_ENTITIES = {}
HA_SWITCH_LABELS = {}  # Optional labels override labels embedded above.

# Configure only real per-appliance sensors. Optional keys include:
#   dishwasher_running, dishwasher_duration, washer_time, dryer_time,
#   washer_power, dryer_power.
# Power may come from a per-appliance sensor (>1 W indicates running) or a switch.
# A shared circuit meter is not an individual washer/dryer measurement.
# Visibility preferences do not manufacture unavailable or active appliance data.
HA_APPLIANCE_ENTITIES = {}

# Additional HA-owned numeric fields only; native telemetry retains ownership.
HA_SENSOR_ENTITIES = {}

# Optional rich cards. The names below are fictitious structural examples:
# HA_FILTERED_ENTITIES = {
#     "covers": ["cover.example_blind"],
#     "media_players": ["media_player.example_speaker"],
#     "scenes": ["scene.example_evening"],
#     "numbers": ["number.example_level"],
#     "sensors": ["sensor.example_temperature"],
#     "weather": "weather.example_home",
# }
HA_FILTERED_ENTITIES = {}

# Native Water and EV use environment variables, not HA mappings:
# CERBO_PORTAL_ID, WATER_TANK_INSTANCE, WATER_PUMP_INSTANCE, WATER_VALVE_INSTANCE,
# EV_INSTANCE and EVCHARGER_INSTANCE. An explicit native instance 0 is valid;
# unset/auto EV instances select a connected device with usable telemetry.
