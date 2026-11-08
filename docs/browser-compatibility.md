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

Published frontend files are marked `-text` in `.gitattributes`. Git must preserve
these files byte for byte when adding or checking them out, including on Windows
with `core.autocrlf=true`; otherwise line-ending conversion invalidates the
release hashes before browser scenarios start. The import-contract suite also
checks a real disposable Git add/checkout against every manifest hash, preserves
mixed line endings, and verifies that ordinary files still follow Git's normal
line-ending setting. The browser's strict inventory check remains unchanged.

`scripts/browser-scenarios.json` is also marked `-text` because every browser
report binds its original SHA-256. The same real Git checkout regression verifies
the contract bytes and hash with `core.autocrlf=false` and `true`, alongside the
frontend inventory and ordinary text conversion. Producers and the final evidence
collector continue comparing the original contract without line-ending normalization.

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

## Packaged and native qualification layers

The release candidate path supplies the producing job's exact portable frontend
artifact and verifies its source, run, attempt, artifact ID, archive digest and
frontend inventory before running the browser scenarios. Retain the corresponding
package/runtime evidence as well: frontend matrix success alone does not qualify
an entire packaged backend. The product-owned scenario manifest prevents an empty
or shortened scenario set from counting as full acceptance.

The browser matrix continues to block Service Workers to isolate its UI scenarios.
A separate `scripts/pwa_acceptance.cjs` HTTPS lane enables the actual candidate
worker and persistent IndexedDB. Permission decisions and PushManager registration
are explicit browser-test/provider fixtures; that lane does not claim native
consent or operating-system delivery. The worker has no offline asset cache.

A hosted macOS `scripts/native_acceptance_smoke.cjs` lane uses installed Safari
through safaridriver and records its actual runtime identity. It is partial native
smoke. Full native Safari/iOS/Android acceptance separately requires observed
software keyboard/touch where relevant, installed PWA lifecycle, real permission
and subscription transitions, background/resume, original alarm event time,
closed-tab OS display and safe notification click. A native simulator/emulator may
satisfy those scenarios when its actual capabilities and observations are recorded;
Playwright device emulation cannot.

See [native acceptance tools, evidence schema and operator stable preflight](native-browser-acceptance.md).
The strict verifier binds each observation to the exact candidate and fails closed
on missing targets or unsupported/skipped scenarios. Its reviewed evidence must be
checked before protected stable approval; it does not silently reconfigure the
shared release engine or GitHub environment protections.
