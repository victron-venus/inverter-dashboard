# Configuration reference

[README](../README.md) · [Wiki](wiki/Home.md) · [Architecture](architecture.md)

The implementation is defined by [config.py](../src/inverter_dashboard/config.py), [settings_store.py](../src/inverter_dashboard/settings_store.py) and [ha_client.py](../src/inverter_dashboard/ha_client.py). Examples use reserved domain names and fictitious entities. Keep production values in a private service environment, ignored `.env`, ignored `local_config.py` or your secret manager.

## Sources and precedence

Pydantic loads environment variables and an optional `.env` in the working directory; process environment takes precedence over that file. The HA loader reads `local_config.py` from `INVERTER_DASHBOARD_CONFIG` or its supported application locations. Native connection variables belong to the process environment, not to arbitrary assignments in `local_config.py`.

At startup, supported saved connection settings override environment/native defaults and HA file credentials when nonempty. Explicit `--mqtt-host` and `--mqtt-port` then win over saved values. An explicit empty MQTT host is meaningful: it disables direct MQTT. UI visibility settings apply on the next state broadcast; saved connection settings require restart. The port option `--port` controls the web listener, not MQTT.

Do not assume clearing a saved credential also clears a separately configured environment credential: empty stored values do not replace startup connection defaults. Edit the actual authoritative secret source when removing credentials.

## Native connection

- `MQTT_HOST` defaults to `Cerbo`; set it to empty for IGW-only or a disconnected local preview.
- `MQTT_PORT=1883`; `MQTT_USERNAME` and `MQTT_PASSWORD` are optional broker credentials.
- `MQTT_TLS=false`; enable it for a TLS broker and provide `MQTT_CA_CERT` if using a private trust bundle. Broker certificate and hostname verification remain enabled.
- `MQTT_RECONNECT_MIN=1`, `MQTT_RECONNECT_MAX=60` are reconnect backoff bounds in seconds.
- `CERBO_PORTAL_ID` selects a GX portal. Configure it for reliable startup; passive discovery cannot activate a silent broker by itself. A selected portal is not mixed with other portals.
- `GATEWAY_ENABLED=false`, `GATEWAY_URL=""`, `GATEWAY_API_TOKEN=""` configure IGW. The URL must be a verified HTTPS origin with no user information, path prefix, query or fragment. Redirects are refused.
- `GATEWAY_ACCESS_CLIENT_ID` and `GATEWAY_ACCESS_CLIENT_SECRET` must be both empty or both supplied. Native HTTPS does not inherently require Cloudflare Access.
- `GATEWAY_POLL_INTERVAL=2.0` seconds. `SSL_CERT_FILE`/`SSL_CERT_DIR` can provide trusted private CAs for HTTP clients; see [TLS policy](tls-policy.md).

If both transports are configured, reachable MQTT is preferred and IGW is the fallback. Explicit source ownership and generation checks reject late work from a retired connection. A successful IGW snapshot is a local receipt of a complete snapshot, not proof of each sensor's sampling time.

Native bootstrap requests the selected portal's tree. Subsequent keepalives use `suppress-republish` every 20 seconds. Bounded read requests refresh only the configured pump/valve Mode leaves; they are not physical write commands.

## Device selection

`WATER_TANK_INSTANCE=21`, `WATER_PUMP_INSTANCE=1`, `WATER_VALVE_INSTANCE=2` must match your native bridge. Instance zero is valid. `EV_INSTANCE` and `EVCHARGER_INSTANCE` default to automatic selection of the lowest connected numeric instance with usable telemetry. Set an explicit instance to pin a device; an unavailable pinned device does not silently switch to another. Use `auto` or unset the variable to restore discovery.

Water uses native `tank`/`pump` data. Mode values are Auto `0`, forced on `1`, forced off `2`. Control availability is separate from the displayed last value: missing, invalid, retained, old or disconnected authority cannot authorize a write. IGW additionally requires its Water capability. EV uses native vehicle and charger services; watts and kilowatts are distinct fields.

## Web listener and access

`HOST=127.0.0.1`, `WEB_PORT=8080` are defaults. A container normally needs `HOST=0.0.0.0` inside its network namespace plus an explicitly published port. A LAN listener requires an intentional TLS/authentication boundary.

`DASHBOARD_SECRET` is empty by default: this means unprotected management access, not a default password. Generate a random secret locally, store it privately and inject it through your service/secret manager. HTTP API clients can use `Authorization: Bearer …`. The existing browser/WS flow also accepts a token in the URL; exclude query strings from access logs and avoid shared bookmarks. The server rejects cross-origin browser WebSockets and settings writes. Preserve the external Host through your reverse proxy; untrusted forwarded-host/proto values do not grant authority.

`--ssl-cert` and `--ssl-key` configure native HTTPS. Container entrypoint auto-detects `dashboard.crt` and `dashboard.key` in its configuration directory. Use a certificate valid for the requested hostname and an appropriate trusted CA. Never use `curl -k` as an acceptance check.

`SELF_UPDATE_ENABLED=false` is the default. Prefer replacing verified packages/images. Enabling self-update changes the deployment trust model; it is unsuitable for an immutable read-only container. `UPDATE_PIN` concerns that update path, not ordinary device control.

## Home Assistant

Run `bash scripts/init-config.sh`; the resulting `local_config.py` is ignored and created privately. For containers, mount the directory as read-only configuration and set `INVERTER_DASHBOARD_CONFIG` to its mount path. Configure your own URL/token and mappings. A minimal mapping structure is:

```python
HA_DIRECT_CONTROLS = True
HA_POLL_INTERVAL_SEC = 12
HA_SWITCH_ENTITIES = {
    "workbench_light": {
        "entity": "light.example_workbench",
        "label": "Workbench",
        "order": 10,
        "enabled": True,
    },
}
HA_APPLIANCE_ENTITIES = {}
HA_FILTERED_ENTITIES = {}
```

`light.example_workbench` is fictitious. Supply `HA_URL` and `HA_TOKEN` privately before enabling direct integration. No personal Home entity list is built in. Strings and `(entity, label)` pairs are also accepted; `HA_SWITCH_LABELS` overrides embedded labels. Equal order values preserve declaration order. Disabled entries are not polled or authorized; an empty mapping renders no Home buttons.

`HA_BOOLEAN_ENTITIES` and `HA_SENSOR_ENTITIES` cover HA-owned fields only. Do not map native power/Water/EV or controller policy fields such as `only_charging` to HA: owner filtering preserves their native/controller source. `HA_FILTERED_ENTITIES` supports `covers`, `media_players`, `scenes`, `numbers`, `sensors` lists and one `weather` entity. Service calls are restricted to configured/currently available targets; numeric controls respect current bounds. Direct HA controls require a fresh successful HA observation (30 seconds), not just a historical displayed value.

`HA_APPLIANCE_ENTITIES` supports dishwasher running/duration and washer/dryer timers/power. Map real per-appliance entities. Zero, unknown and running are different states; do not use a shared circuit measurement as individual appliance power. `show_dishwasher`, `show_washer`, `show_dryer` control visibility, but do not manufacture activity or telemetry. `HA_POLL_TIMEOUT=20` and `HA_REQUEST_TIMEOUT=15` are request bounds; do not set polling so slowly that control authority is continually stale.

## Durable preferences

`INVERTER_DASHBOARD_SETTINGS_FILE` selects a writable JSON file, for example `/var/lib/inverter-dashboard/settings/dashboard_settings.json`. Without it the path is under `INVERTER_DASHBOARD_CONFIG`, or the application default. A read-only Secret/config mount cannot store preferences.

Supported preferences include `camera_topic`, `show_daily_stats`, `show_header_toggles`, `show_ha_sensors`, `show_ha_numbers`, `show_batteries`, `show_solar_production`, `show_active_loads`, `show_ev`, `show_washer`, `show_dryer`, `show_dishwasher`, `show_home_section`, `show_ha_covers`, `show_ha_media`, `show_ha_scenes`, `show_ha_weather`, plus supported MQTT/HA connection overrides. Unknown keys and invalid types are rejected. The MQTT port must be an integer in 1–65535, not a boolean.

A new explicitly configured parent uses mode 0700; writes use a same-directory 0600 temporary file, file sync, atomic replacement and POSIX directory sync. Storage failure is returned instead of claiming success. Credentials remain plaintext in this private file; API responses mask them, and round-tripping `***` preserves the real value. Restrict backups as carefully as live files.

## Web Push and camera events

`WEB_PUSH_ENABLED=false`, `WEB_PUSH_DATA_DIR=""`, `WEB_PUSH_SUBJECT=https://github.com/victron-venus/inverter-dashboard` configure server push. Enabling it requires a private persistent directory. Keep a single writer and preserve keys/subscriptions through upgrades; [Notifications](notifications.md) describes the supported platforms and limits.

`CAMERA_TOPIC` is empty by default and enables the existing MQTT camera-event path when configured. Camera notifications are distinct from native alarms and Web Push categories; a configured topic does not itself prove browser permission or end-to-end notification delivery.
