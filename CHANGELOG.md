# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **PV inverters not filling solar total**: discover `N/.../pvinverter/...` like
  desktop (CustomName/Serial/L1|L2), sum into `pv_inverter_total` /
  `solar_total`, and publish Cerbo keepalive immediately on connect.
- **Version shows "Web Dev"**: read `VERSION` from package-adjacent /
  `/app/VERSION` paths used by non-editable Docker installs (was falling back
  to `dev`).
- **Header control flags missing / wrong source**: toggle and state use Cerbo
  MQTT `inverter/state.booleans` bare keys (desktop parity), never HA
  `input_boolean` / `binary_sensor` mirrors.
- **Top daily status row**: refreshed Vue SPA includes DailyStats; row stays
  visible when yesterday or forecast has data even if today is still 0.
- **Console panel removed** from the bottom of the dashboard.

### Fixed
- **Live telemetry zeros after MQTT_SLIM_STATE**: dashboard now reads Cerbo Venus
  MQTT (`system` / `battery` / `solarcharger` / `vebus` / `acload` / `pvinverter`)
  for grid, consumption, bank SoC, solar, setpoint/mode, and active loads — the
  same durable sources as inverter-desktop — instead of relying on slim
  `inverter/state` for those tiles. Daily solar / battery-out from the daemon
  still work.
- **Active Loads blink-and-clear**: slim `inverter/state` no longer replaces
  `current_state` wholesale; Cerbo acload maps are re-applied after each daemon
  tick, and load power updates always refresh the UI.
- MQTT message loop never connected: passing `tls_insecure` without an SSL
  context made paho raise `ValueError`, killing the loop task silently at
  startup. TLS params are now only passed when `MQTT_TLS` is enabled.

### Changed
- k3s ConfigMap: `MQTT_HOST=192.168.160.150`, `CERBO_PORTAL_ID=b827ebea1ece`
  (Cerbo broker + portal keepalive / water+EV).
- Subscribes to `inverter/portal` and publishes `R/<portal>/keepalive`.

### Added
- **EV system via Cerbo MQTT** — `dbus-ev` / `dbus-evcharger` integration:
  - New `CERBO_PORTAL_ID` config subscribes to EV topics:
    - `N/<portal>/ev/<instance>/Soc` → `car_soc` (%)
    - `N/<portal>/ev/<instance>/Ac/Power` → `ev_power` (W)
    - `N/<portal>/evcharger/<instance>/Ac/Power` → `ev_charging_kw` (kW)
  - Config keys: `EV_INSTANCE` (default 22) and `EVCHARGER_INSTANCE` (default 40)
  - `MqttState.handle_ev()` decodes topics into the state payload;
    subscriptions added in `_subscribe_topics()`
  - WebSocket model `InverterState` already exposes `ev_charging_kw`,
    `ev_power`, `car_soc` fields — no UI schema change needed
  - New test file `tests/test_server_ev.py` covers all decode paths and
    instance/portal gating

### Changed
- **Water system migrated from Home Assistant to dbus-pump via Cerbo MQTT** (no HA):
  - New `CERBO_PORTAL_ID` config (+ `WATER_TANK_INSTANCE` / `WATER_PUMP_INSTANCE` /
    `WATER_VALVE_INSTANCE`, defaults 21/1/2) subscribes to
  - `N/<portal>/tank/<N>/Level` and `N/<portal>/pump/<N>/State`
  - `HA_WATER_VALVE_ENTITY` / `HA_PUMP_SWITCH_ENTITY` removed from site/local config;
    water keys are no longer overlaid from HA or zeroed when HA is down
- **EV terminology clarified**: `ev_charging_kw` (kW, from evcharger) vs
  `ev_power` (W, from vehicle); neither comes from HA sensor entities anymore.
  Update `local_config.example.py` comments accordingly — removed the stale
  "no D-Bus standard" note since `dbus-ev`/`dbus-evcharger` now publish these.
