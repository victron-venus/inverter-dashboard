# Warnings and system notifications

[Wiki home](Home.md) · [Troubleshooting](Troubleshooting.md)

## Event time is not connection time

The dashboard preserves a native alarm's occurrence time. Returning to the home network must not turn an hour-old alarm into “just now”. If the source has no valid time, the dashboard keeps it unknown. Connection receipt timestamps are used for freshness checks and are never substituted as alarm occurrence times.

Inactive but unacknowledged Victron notifications can still appear as warnings. Acknowledged history is not replayed as a new active alarm. Local dismissal affects the dashboard view; device acknowledgement/silence is a separate, guarded command. Clicking a system notification only opens the dashboard and does not operate equipment.

## Enable Web Push deliberately

Enable server push with `WEB_PUSH_ENABLED=true` and a persistent private `WEB_PUSH_DATA_DIR`, then grant browser permission through the settings interface. The browser must confirm subscription registration with the server. Browser/OS secure-context and installation requirements differ; test the actual target platform.

The server sender supports Linux and macOS Apple Silicon. Windows and Intel macOS hosts keep normal telemetry/banners but report `unsupported_platform` for server push. Their browsers can receive from a supported remote server. Missing/inaccessible/corrupt initialized storage must not be “fixed” by deleting keys: this changes identity and loses subscriptions. Diagnose ownership, mount persistence and single-writer locking first.

## Avoid replay and false events

Existing warnings prime silently on initial connection and reconnect. MQTT has a silent ten-second hydration period. Missing or old occurrence times do not create a new push; times more than 30 seconds in the future are rejected, allowing a bounded clock difference between the source and server. Additional categories observe selected charger power, pump/valve states and actual native battery SoC after a fresh same-source baseline. Retained/invalid data does not refresh that baseline. Estimated voltage-derived display SoC and near-zero grid watts never invent a low-SoC or grid-loss event.

Messages expire 300 seconds after their source time and use bounded retries. Source retirement cancels its pending/in-flight work; provider-accepted messages cannot be recalled. Subscription deletion or preference changes remove associated pending work. Preserve the private store across upgrades for identity and deduplication.

## Understand delivery evidence

A test API response of `202`/`queued` proves the server queued work. A provider success proves provider acceptance. Neither proves that the browser processed the push or that the OS displayed a banner. Diagnose queue, provider, service worker and OS permissions separately. Never post endpoint capability URLs or private store contents in an issue.

The [detailed lifecycle guide](https://github.com/victron-venus/inverter-dashboard/blob/main/docs/notifications.md) includes an accessible SVG, APIs, provider restrictions and expiry limits. [Frontend provenance](https://github.com/victron-venus/inverter-dashboard/blob/main/docs/frontend-assets.md) explains how to identify the exact service-worker build.
