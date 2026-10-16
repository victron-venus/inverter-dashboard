# Configuration and persistence

[Wiki home](Home.md) · [Troubleshooting](Troubleshooting.md)

## Select a native connection

For direct MQTT, set `MQTT_HOST`, `MQTT_PORT` and `CERBO_PORTAL_ID`, plus broker credentials and TLS trust when required. An explicit portal is important on silent modern brokers: passive discovery does not itself request the first native tree. Subsequent keepalive runs every 20 seconds with suppressed full-tree republication, plus narrow reads of the configured Water Mode leaves.

For IGW-only, set `MQTT_HOST=""`, `GATEWAY_ENABLED=true`, `GATEWAY_URL` and `GATEWAY_API_TOKEN`. The URL must be a verified HTTPS origin with no path prefix or redirect. Cloudflare Access client ID/secret are optional as a complete pair for routes that require them. With both transports configured, reachable MQTT is preferred. Reconnection replaces source authority rather than combining old and new snapshots.

Native defaults are Water tank/pump/valve instances 21/1/2. EV and charger default to automatic lowest usable connected numeric instance; explicit pins, including zero, do not silently fall back to another device. Check the bridge's actual device instances before changing these values.

## Separate configuration from writable state

Environment and an optional `.env` supply connection configuration. `local_config.py` supplies optional HA mappings; keep it ignored and private. Saved connection overrides apply at startup, then explicit MQTT CLI arguments take precedence. UI visibility preferences apply on the next broadcast; connection changes require restart.

Use `INVERTER_DASHBOARD_CONFIG` for a mounted configuration directory. If that mount is read-only, place preferences elsewhere with `INVERTER_DASHBOARD_SETTINGS_FILE`, for example `/var/lib/inverter-dashboard/settings/dashboard_settings.json`. Provision the containing persistent volume for the runtime identity. New private settings directories/files use 0700/0600; writes are atomic and report storage failures. API masking does not encrypt credentials stored in that file.

For Web Push, configure a separate private `WEB_PUSH_DATA_DIR` on persistent local storage and one process/replica. Preserve the entire store during upgrades. Do not use temporary container storage or a publicly served path. See [Notifications](Notifications.md).

## Add only your HA entities

Create `local_config.py` with `bash scripts/init-config.sh`, then supply your HA URL/token privately. Direct operations require `HA_DIRECT_CONTROLS=True`, successful fresh HA observations and configured targets. `HA_SWITCH_ENTITIES` determines Home buttons and accepts entity strings, pairs or objects with label/order/enabled. Empty means no buttons; disabled entries cannot be commanded. Rich entity lists belong in `HA_FILTERED_ENTITIES`; appliances use `HA_APPLIANCE_ENTITIES`.

Do not map controller flags or native power, Water and EV readings to HA mirrors. An appliance timer or shared outlet meter does not establish individual appliance activity. Visibility settings do not create missing measurements.

The [complete configuration reference](https://github.com/victron-venus/inverter-dashboard/blob/main/docs/configuration.md) lists defaults, precedence, examples, TLS and authentication. The [k3s guide](https://github.com/victron-venus/inverter-dashboard/blob/main/deploy/k3s/README.md) contains public templates that must be adapted privately before deployment.
