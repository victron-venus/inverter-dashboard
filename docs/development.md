# Development guide

[README](../README.md) · [Contributing](../CONTRIBUTING.md) · [Architecture](architecture.md)

Use a topic branch and Python 3.12 or newer with `uv`. `uv sync --locked --extra test` installs the project and test dependencies from the lockfile. `bash scripts/ci.sh` is the authoritative local validation entry point: it selects the checked-in security tooling and runs lint, formatting, tests and coverage. Run `pre-commit run --files <changed paths>` for applicable hooks. Do not weaken required CI or release policy to make a change green.

The application minimum is declared in [pyproject.toml](../pyproject.toml).
[Backend CI](../.github/workflows/ci.yml) currently selects Python 3.12;
[frozen executable packaging](../.github/workflows/release-build.yml) selects
Python 3.13, and the [Dockerfile](../Dockerfile) pins a Python 3.14 Alpine
image. These are separate build environments. Check the committed files and
the chosen release's receipts for its exact toolchain.

## Safe local workflow

Start without native sources using `GATEWAY_ENABLED=false uv run --locked inverter-dashboard --mqtt-host ""`. This lets you inspect the app without reaching a broker or gateway. Tests should use isolated temporary settings and mocked MQTT/HTTP clients. Existing configuration under your home directory or a production mount is not a fixture. Never use a real actuator write, alarm acknowledgement or subscription send merely to test documentation.

For a backend fix, first reproduce the exact failure and add a regression that proves changed behavior. Run the affected tests and relevant broader ownership/lifecycle tests; then use required CI for final policy coverage. Distinguish mocked source correctness from hardware/browser/OS acceptance. A green unit test is not evidence of physical completion.

## Source map

- `server.py`: FastAPI routes, startup/shutdown, selected native source and subscriptions.
- `cerbo.py`: native MQTT/snapshot reduction, device identity, availability and provenance.
- `gateway.py`: IGW HTTPS access, bounded requests and source-generation cancellation.
- `controller_commands.py`, `websocket_handler.py`: command validation, freshness, correlation and payload assembly.
- `ha_client.py`: local entity mappings, direct HA reads and allowlisted service calls.
- `settings_store.py`: typed preferences, masking, private atomic persistence and restart overrides.
- `notifications.py`, `push_events.py`, `push_observer.py`, `push_service.py`, `push_store.py`, `push_transport.py`: warning reduction and independent server push lifecycle.
- `tls_*.py`: verified certificate/key policy and transport adapters; see [TLS policy](tls-policy.md).

## Frontend and internationalization

The shared [Vue repository](https://github.com/victron-venus/inverter-dashboard-vue) owns UI behavior and catalogs. Python serves exact recorded SPA bytes. Fix UI source there, review it, qualify its official artifact, then adopt its entire inventory and truthful `source-info.json`; never patch minified JS in this repository. [Frontend assets](frontend-assets.md) documents the boundary.

Documentation exists in en/ru/uk/de/fr/nl; UI availability depends on the adopted Vue version. [i18n guidance](i18n.md) explains source-language keys, units and device-provided text. Keep translated setup/security/troubleshooting instructions useful, not merely a translated title.

## Documentation validation

Check relative Markdown links against actual files and anchors, parse every SVG as XML, and ensure diagrams have a `title`, `desc`, `viewBox` and accessible labelling. SVGs must be self-contained: no scripts, external fonts/images or event handlers. Keep technical diagrams editable in source, with a textual equivalent in the linked guide. Check examples for nonexistent options and personal values against the actual config/API implementation.

The wiki source is under [docs/wiki](wiki/Home.md). A GitHub wiki is a separate repository; changing these files does not publish it automatically. Publish the reviewed pages through the repository owner's normal process, retaining the same safety statements and release identity.

## Releases and dependencies

[RELEASING.md](../RELEASING.md) and [.release-policy.json](../.release-policy.json) define channel/gate behavior. A saved plan binds source, version and platform outputs. Preserve all required targets and immutable verification evidence; a partial successful matrix is not a release. Never infer stable publication from a merged PR.

`http-ece` uses the reviewed vendored pure-Python wheel because its upstream release lacks an official wheel. Its provenance/license live in [vendor/http-ece](../vendor/http-ece). The offline rebuild script uses hash-pinned inputs; this is not permission to alter dependency crypto or disable the no-build packaging policy. The container build has its own reviewed toolchain and is distinct from frozen executable packaging.
