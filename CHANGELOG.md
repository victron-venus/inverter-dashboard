# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- MQTT message loop never connected: passing `tls_insecure` without an SSL
  context made paho raise `ValueError`, killing the loop task silently at
  startup. TLS params are now only passed when `MQTT_TLS` is enabled.

### Changed
- **Water system migrated from Home Assistant to dbus-pump via Cerbo MQTT** (no HA):
  - New `CERBO_PORTAL_ID` config (+ `WATER_TANK_INSTANCE` / `WATER_PUMP_INSTANCE` /
    `WATER_VALVE_INSTANCE`, defaults 21/1/2) subscribes to
    `N/<portal>/tank/<N>/Level` and `N/<portal>/pump/<N>/State`
  - `HA_WATER_VALVE_ENTITY` / `HA_PUMP_SWITCH_ENTITY` removed from site/local config;
    water keys are no longer overlaid from HA or zeroed when HA is down
