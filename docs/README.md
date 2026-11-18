# Documentation

The Python inverter dashboard: a service that serves a reviewed browser UI plus a
wiki that documents its configuration and troubleshooting.

## Understand the service

- [Architecture and ownership](architecture.md) — how the backend, frontend and clients divide responsibility.
- [Configuration reference](configuration.md) — the settings the service reads.
- [Embedded frontend assets](frontend-assets.md) — the committed Vue SPA the Python package serves.
- [Languages and release boundaries](i18n.md) — the six languages and what ships with a release.
- [Development guide](development.md) — how to work on the service locally.
- [Process and results](process-and-results.md) — what a change goes through before it is visible.

## Browser behaviour

- [Notifications: event time, banners and Web Push](notifications.md) — how an event's time is preserved and pushed.
- [Embedded frontend browser acceptance](browser-compatibility.md) — the shared TypeScript/browser gate.
- [Native browser, installed PWA and notification acceptance](native-browser-acceptance.md) — the acceptance each surface needs.

## Wiki source

- [Inverter Dashboard wiki](wiki/Home.md) — the repository-maintained source of the dashboard wiki.
- [Configuration and persistence](wiki/Configuration.md) — the wiki configuration page.
- [Warnings and system notifications](wiki/Notifications.md) — the wiki notifications page.
- [Development and documentation maintenance](wiki/Development.md) — how the wiki is maintained.
- [Process and results](wiki/Process-and-results.md) — the wiki process page.
- [Troubleshooting without changing equipment](wiki/Troubleshooting.md) — the wiki troubleshooting page.
- [Wiki navigation](wiki/_Sidebar.md) — the wiki sidebar links.

## Assurance

- [Architecture, security evidence and roadmap](project-assurance.md) — the evidence scope and planned work.
- [TLS certificate policy](tls-policy.md) — the verification each outbound client performs.
- [Packaged browser qualification network](PACKAGED-BROWSER-NETWORK.md) — the isolated Docker bridge the qualification containers use.
- [OpenSSF Best Practices evidence: inverter-dashboard](openssf-evidence.md) — the index behind the assessment.

## Releases

- [Release version inputs](release-versioning.md) — the `versioning` fields that identify a release.
- [CI and release operations](release-workflow.md) — `.release-policy.json` and the required status.
- [CI orchestration](CI-ORCHESTRATION.md) — how the quality gate and release pipeline divide the events.
- [Version plans and manifest synchronization](VERSIONING.md) — the versioning opt-in.
- [Release-candidate fuzz testing](fuzzing.md) — the illustrated RC fuzzing process.
- [Fuzzing release candidates](rc-fuzzing.md) — the reusable bounded fuzz workflow.
- [Release read recovery and rerun validation](release-read-safety.md) — the two fixes carried from the shared toolkit.
- [Early final-build runner check](runner-preflight.md) — why the check runs before the final build.
