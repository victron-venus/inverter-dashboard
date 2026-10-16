# Embedded frontend assets

Python serves the recorded shared Vue SPA under `src/inverter_dashboard/static`. [source-info.json](../src/inverter_dashboard/static/source-info.json) is the machine-readable source/release identity and complete file inventory. The current recorded artifact is Vue `2.2.7-beta.9`, source `3d4a03fd3401334ac9e69f6d103caff41dc243ae`; always consult the manifest after an upgrade rather than treating this sentence as a runtime version check.

The backend accepts the flat `static/` layout and the legacy `static/dist/` layout, mounting Vite assets at `/assets` and a compatibility `/static` route. Notification worker/manifest/icon use explicit root routes with no-cache headers and root worker scope. Configuration and private state must never be copied into either static tree.

## Official adoption

1. Fix and review source in [inverter-dashboard-vue](https://github.com/victron-venus/inverter-dashboard-vue).
2. Obtain the qualified official artifact and verify its source, release plan, full inventory, checksums and release receipts.
3. Copy its exact bytes into this repository and remove only obsolete files from the prior recorded inventory. Do not rebuild, reformat generated JS, or patch a minified asset to silence a checker.
4. Record truthful source/release information and per-file size/hash in `source-info.json`. Preserve the original upstream verification evidence.
5. Run backend asset inventory/route/package tests and ordinary CI, then review and release this backend separately.

A local frontend build is useful for development but is not an official release artifact. Do not label its provenance as published/verified. Backend source, Vue source, Vue release and backend release have separate identities. Localization changes follow this same path; translated repository documentation alone does not install new catalogs.

The package tests check inventory, hashes and served asset contracts. Browser checks additionally need the actual approved release, expected route/origin and fresh observed UI. A previously registered worker or an old tab is not proof of which new build is being served.
