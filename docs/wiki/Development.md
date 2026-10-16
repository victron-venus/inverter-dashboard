# Development and documentation maintenance

[Wiki home](Home.md) · [Configuration](Configuration.md)

Use a focused branch and reviewed PR. The Python backend and shared Vue UI have separate source and release identities. Keep changes in the owning repository; do not patch minified generated files in the Python package.

## Reproduce safely

Install Python 3.12+ and `uv`, then use the lockfile. For a local preview, use `GATEWAY_ENABLED=false uv run --locked inverter-dashboard --mqtt-host ""`; the explicit empty CLI host takes precedence over saved MQTT settings. Keep the default loopback listener and isolate configuration and storage so existing HA credentials are not loaded. Normal tests must not discover private credentials or send commands to equipment.

Add meaningful regressions for bugs: invalid and stale input, source switches around awaits, storage failure, request correlation and before/after state. Run the affected scope, then the repository's required checks. `bash scripts/ci.sh` defines local security/lint/format/test/coverage behavior. Do not change policy thresholds to avoid a failure.

## Maintain source ownership

Native reduction belongs to `cerbo.py`; connection lifecycle and HTTP endpoints belong to `server.py` and `gateway.py`; command validation/dispatch belongs to `controller_commands.py` and `websocket_handler.py`. HA mappings and targets belong to `ha_client.py`; durable local preferences belong to `settings_store.py`. Warning and Web Push processing have dedicated modules so they continue independently of an open browser tab.

Controller command acceptance is not physical acknowledgement. Maintain final source-generation checks after awaited work. Native timestamps and local receipt timestamps serve different purposes. Never “repair” an unknown time by fabricating now.

## Adopt an official frontend

Fix and review Vue source, verify its official release artifact, then copy the exact file inventory and truthful provenance into Python. Remove only obsolete recorded assets. Verify file hashes, package metadata and served routes. Do not claim a local build is an officially published artifact. Language support follows the same adoption process.

## Keep documentation useful

Update all six README languages when behavior changes. Keep environment variables, enum identifiers and wire formats untranslated. Include restart requirements, storage locations, security limitations and real troubleshooting, not only feature slogans. SVG diagrams must remain editable, self-contained and accessible through title/description metadata plus a text explanation.

Check Markdown targets/anchors, parse SVG XML and verify that examples match the current implementation. The hosted wiki is published separately from these repository source pages; keep its navigation and links consistent with the reviewed source revision.

The [developer guide](https://github.com/victron-venus/inverter-dashboard/blob/main/docs/development.md), [contribution policy](https://github.com/victron-venus/inverter-dashboard/blob/main/CONTRIBUTING.md) and [release strategy](https://github.com/victron-venus/inverter-dashboard/blob/main/RELEASING.md) provide the complete workflow. Source tests, official artifact verification and live browser/OS/hardware acceptance are separate evidence.
