# Embedded frontend browser acceptance

The required quality gate calls `ci.yml`, including the shared TypeScript/browser
workflow. Five required targets exercise Chromium, Firefox and WebKit on Linux,
Chromium on Windows and WebKit on macOS. Beta releases retain those five
required targets. Scheduled source audits, manual audits without a release
channel and RC release runs select all 35 targets: three pinned Playwright generations across three operating
systems, plus Chrome/Edge Stable and Linux Beta channels. Hosted runners remain
the default; this change does not switch runner mode.

`scripts/browser-acceptance.cjs` serves the real committed SPA under `src/inverter_dashboard/static` on
an ephemeral loopback address. Before opening a browser, it verifies every file
against the committed frontend provenance manifest and rejects missing, extra,
altered or linked files. Browser evidence includes the frontend source commit,
release version and original archive digest in addition to the backend commit,
workflow run and actual browser version.

The adapter reuses the dashboard user flows developed in
[inverter-dashboard-vue](https://github.com/victron-venus/inverter-dashboard-vue/blob/00f406c278cdd53c4da68eea68eff6b606f94308/scripts/browser-acceptance.cjs).
Keep the two backend adapters aligned when those common frontend flows change.
It asserts live telemetry/charts, updates, persisted theme, modal focus/Escape,
invalid and valid settings, persisted visibility, rejected commands, reconnect,
narrow layout, public read-only controls, and absence of browser errors or
unexpected external traffic. All scenarios must pass; none are skipped to
accommodate an older embedded bundle.

Backend HTTP/WebSocket traffic is synthetic and isolated. These checks qualify
the embedded frontend in real browsers; the existing backend unit/integration
checks separately qualify the server implementation. Native Safari, Firefox ESR,
iOS/Android devices, MQTT hardware and native webviews require separate evidence.

For a local run, install an isolated toolchain and its browser, then point the
adapter to that module:

```bash
npm ci --prefix tools/browser-compatibility/playwright-1-64-0 --ignore-scripts --omit=optional
node tools/browser-compatibility/playwright-1-64-0/node_modules/playwright/cli.js install chromium
CI_PLAYWRIGHT_MODULE="$PWD/tools/browser-compatibility/playwright-1-64-0/node_modules/playwright" \
  CI_BROWSER_ENGINE=chromium node scripts/browser-acceptance.cjs
```

No application rebuild or application npm install is needed. Switch the engine
to `firefox` or `webkit` after installing it. Traces, screenshots and diagnostic
JSON are saved under ignored `.compatibility/` locally and uploaded as
`browser-compatibility-*` artifacts in CI.
