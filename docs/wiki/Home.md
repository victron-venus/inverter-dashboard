# Inverter Dashboard wiki

This is the repository-maintained source of the Python dashboard wiki. It describes the Python service, not the separately packaged Go or native Desktop application. Publication to the GitHub wiki is a separate maintainer action; editing this directory does not update the hosted wiki automatically.

## Start with the right layer

The dashboard displays native Cerbo GX telemetry through direct MQTT or an HTTPS inverter-gateway. The optional inverter-control daemon supplies policy controls, history, daily statistics and forecasts. Home Assistant adds locally configured appliances and devices. These sources have different ownership and freshness rules: a working browser connection alone does not prove that a device reading or command capability is current.

![Python dashboard sources, command boundaries and persistent state](../assets/architecture.svg)

Use [Configuration](Configuration.md) for first installation and persistent state, [Notifications](Notifications.md) for event time and Web Push, [Troubleshooting](Troubleshooting.md) for symptom-based checks, and [Development](Development.md) for source changes and release evidence.

## Operational essentials

1. Choose the native source deliberately. Configure the portal for direct MQTT; clear `MQTT_HOST` when using IGW-only. With both configured, reachable MQTT wins.
2. Protect remote access with HTTPS and a strong dashboard secret. Empty `DASHBOARD_SECRET` leaves management endpoints unprotected.
3. Keep connection secrets outside Git and outside static assets. Configure writable persistent preferences separately from read-only connection configuration.
4. Preserve Web Push state, if enabled, across upgrades. It includes the server's identity and browser subscriptions, not just disposable cache.
5. Treat unsupported/stale controls as unavailable. An accepted request is not a confirmed physical action.
6. Use the exact approved backend and Vue artifact identities for acceptance. A successful source test is not live browser, OS-notification or hardware qualification.

## Reading the interface

A zero is a measurement; unknown and stale are separate conditions. Warning ages derive from source occurrence times. Inactive unacknowledged alarms may remain visible until acknowledged or dismissed according to their lifecycle. Daily grid totals carry partial-day provenance rather than inventing missing hours. Home buttons come from local configuration; appliance cards require suitable visibility and source data.

The repository provides English, Russian, Ukrainian, German, French and Dutch READMEs. UI translation availability depends on the separately released and adopted Vue package. Consult the [repository language guide](https://github.com/victron-venus/inverter-dashboard/blob/main/docs/i18n.md) and its current `source-info.json` before claiming a language is installed.

Detailed source-linked guides and editable SVG diagrams live in the [repository documentation](https://github.com/victron-venus/inverter-dashboard/tree/main/docs). Security reports follow the [private reporting policy](https://github.com/victron-venus/inverter-dashboard/blob/main/SECURITY.md).

[Process and results](Process-and-results.md) explains the source-to-installation workflow, the MQTT CLI and Home dictionary corrections, and a synthetic event-time comparison. Each diagram distinguishes the intended process from evidence of completion.
