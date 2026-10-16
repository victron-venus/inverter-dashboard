# Troubleshooting without changing equipment

[Wiki home](Home.md) · [Configuration](Configuration.md) · [Notifications](Notifications.md)

Start with the exact backend version, served Vue source/build, selected transport and observed symptom. Use sanitized read-only evidence. Do not post `.env`, `local_config.py`, private state, Web Push endpoint URLs, query tokens or complete environment/log dumps.

## Page opens but measurements are missing

`/health/live` only proves that the HTTP event loop answers. `/api/state` without a valid secret may legitimately return health/connectivity only. An authorized response adds the filtered live state. Check `data_source`, `mqtt_connected`, `gateway_connected`, `native_connected` and telemetry quality separately.

For IGW-only, ensure `MQTT_HOST` is empty: its default is not empty. Check the final HTTPS origin, trust chain and complete optional Access credential pair. A browser reaching the gateway does not prove that the dashboard pod can reach it. Do not disable TLS verification to hide a hostname or trust problem.

For MQTT, check the configured portal and broker read/request permissions. A silent broker may need an explicit portal to bootstrap. Another portal's traffic must not populate this dashboard. Measured zero is valid; missing or disconnected readings should not be forced to zero.

## Controls are grey or do not confirm

Check the exact target's capability, current connection and recent authoritative observation. Header/controller, Water and direct HA controls have different owners. A correctly displayed old state is insufficient authority for a new write. Retained, malformed or more-than-30-second-old control observations do not authorize commands.

After reconnect, wait for a valid fresh source update. Do not modify a disabled HTML element to bypass the check. For an uncertain command, inspect subsequent authoritative state before considering a retry. Setpoint Override requires matching later request/value status and may time out without proving failure at the device. Tariff revision conflicts require reloading the current plan.

## Home or appliance cards differ from expectations

Review your private `HA_SWITCH_ENTITIES` list and enabled/order values. There is no personal Home list built into the service. Check direct HA configuration and current entity availability. Disabled/unknown entities are not an off state.

Review appliance mappings and visibility preferences. A timer alone does not establish active operation, and a shared circuit reading cannot be attributed to one appliance. Do not assign native Water/EV/controller fields to HA mirrors to make a missing card appear.

## Preferences disappear or fail to save

Check `INVERTER_DASHBOARD_SETTINGS_FILE`, runtime UID permissions, free space and volume persistence. A read-only Secret mount cannot receive writes. An `emptyDir` or container writable layer does not survive all replacement scenarios. Masked `***` values preserve existing secrets; they are not the actual token. Connection changes apply on restart, unlike visibility settings.

## Banners are old or system notifications are absent

Compare source occurrence time with the current time and timezone. An old unacknowledged warning may be intentionally visible. Web Push reconnect priming suppresses historical alarms. Verify browser permission, server subscription, exact worker build and OS settings independently. Queued and provider-accepted are intermediate outcomes, not proof of a displayed banner.

## UI seems stale after an update

Compare served assets/build identity with the approved backend's `source-info.json`. Old tabs, reverse-proxy caching and workers are distinct layers. Do not delete server notification state as a cache-clearing step. A newly translated README is not proof that translated UI assets have been released and adopted.

Report remaining bugs with minimal redacted reproduction, exact versions, expected behavior and actual evidence. Private vulnerabilities follow [SECURITY.md](https://github.com/victron-venus/inverter-dashboard/blob/main/SECURITY.md), not a public diagnostic dump.
