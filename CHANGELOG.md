# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- MQTT message loop never connected: passing `tls_insecure` without an SSL
  context made paho raise `ValueError`, killing the loop task silently at
  startup. TLS params are now only passed when `MQTT_TLS` is enabled.

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
