#!/usr/bin/env node
/** Exercise the built SPA with synthetic backend traffic in the selected real browser. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const { setTimeout: delay } = require('node:timers/promises')

const root = path.resolve(__dirname, '..')
// User flows follow inverter-dashboard-vue; this adapter serves only the exact
// frontend bytes committed in this backend repository. Backend integration is
// independently required by the repository's existing CI.
const crypto = require('node:crypto')
const application = JSON.parse(fs.readFileSync(path.join(root, '.github/browser-application.json'), 'utf8'))
assert(['python', 'go'].includes(application.manifestKind), 'Unsupported frontend provenance format')
const dist = fs.realpathSync(path.resolve(root, application.distribution))
const manifestFile = fs.realpathSync(path.resolve(root, application.manifest))
assert(dist.startsWith(root + path.sep) && manifestFile.startsWith(root + path.sep), 'Frontend paths must remain inside checkout')
const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'))
assert.equal(manifest.repository, 'https://github.com/victron-venus/inverter-dashboard-vue')
const entries = application.manifestKind === 'python'
  ? manifest.files.map(file => [file.path, file.sha256])
  : Object.entries(manifest.sha256)
const expected = new Map(entries)
assert(expected.size > 0 && expected.size === entries.length, 'Frontend inventory must be nonempty and unique')
const actual = new Map()
function inspectFiles(directory) {
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, item.name)
    assert(!item.isSymbolicLink(), 'Frontend must not contain links')
    if (item.isDirectory()) inspectFiles(file)
    else {
      assert(item.isFile(), 'Frontend must contain only regular files')
      if (file === manifestFile) continue
      const relative = path.relative(dist, file).split(path.sep).join('/')
      actual.set(relative, crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'))
    }
  }
}
inspectFiles(dist)
assert.deepEqual([...actual.keys()].sort(), [...expected.keys()].sort(), 'Frontend inventory differs from recorded release')
for (const [file, hash] of expected) {
  assert.match(hash, /^[0-9a-f]{64}$/)
  assert.equal(actual.get(file), hash, `Frontend bytes changed: ${file}`)
}
const frontend = {
  sourceCommit: manifest.source_commit || manifest.commit,
  version: manifest.package_version,
  archiveSha256: manifest.archive_sha256,
  filesVerified: expected.size,
}
assert.match(frontend.version, /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/)
assert.match(frontend.sourceCommit, /^[0-9a-f]{40}$/)
assert.match(frontend.archiveSha256, /^[0-9a-f]{64}$/)
console.log(`Verified embedded SPA ${frontend.version}, ${frontend.filesVerified} files`)
const diagnostics = path.resolve(process.env.CI_BROWSER_DIAGNOSTICS || '.compatibility/local')
const evidence = path.resolve(process.env.CI_BROWSER_EVIDENCE || path.join(diagnostics, 'result.json'))
const engine = process.env.CI_BROWSER_ENGINE || 'chromium'
const channel = process.env.CI_BROWSER_CHANNEL || ''
const modulePath = process.env.CI_PLAYWRIGHT_MODULE
assert(modulePath && path.isAbsolute(modulePath), 'Set CI_PLAYWRIGHT_MODULE to the isolated toolchain')
assert(fs.existsSync(path.join(dist, 'index.html')), 'Build the real SPA before browser acceptance')
const playwright = require(modulePath)
assert(['chromium', 'firefox', 'webkit'].includes(engine), 'Unsupported browser engine')
fs.mkdirSync(diagnostics, { recursive: true })
fs.rmSync(evidence, { force: true })

const fixture = {
  state: {
    data_source: 'mqtt', native_connected: true, mqtt_connected: true,
    controller_controls_available: true, controller_observed_at: Date.now() / 1000,
    dry_run: false, gt: 420, g1: 200, g2: 220, tt: 850, t1: 400, t2: 450,
    solar_total: 1200, battery_soc: 73, battery_power: -300, battery_voltage: 51.2,
    battery_current: -5.8, setpoint: 25, inverter_state: 'Inverting', uptime: 7200,
    loads: { '1': 200, '2': -100, '3': 5 },
    load_names: { '1': 'Fixture refrigerator', '2': 'Fixture solar', '3': 'Fixture router' },
    batteries: [{ instance: 0, name: 'Fixture battery', soc: 73 }],
    features: { ha: false },
    telemetry: { quality: 'live', source: 'mqtt', observed_at: new Date().toISOString() },
    ui_config: { settings: { camera_topic: '', mqtt_port: 1883, show_active_loads: true } },
  },
  reject: false, paused: false, commands: [], sockets: new Set(), connections: 0,
}
const scenarios = []
const failures = []
const browserErrors = []
const blockedRequests = []
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json', '.woff2': 'font/woff2', '.png': 'image/png', '.ico': 'image/x-icon' }
const server = http.createServer((req, res) => {
  const route = new URL(req.url, 'http://localhost').pathname
  res.setHeader('Cache-Control', 'no-store')
  if (route === '/api/state') {
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify({ ...fixture.state, native_connected: !fixture.paused, mqtt_connected: !fixture.paused }))
    return
  }
  if (route.startsWith('/api/notifications/')) {
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify({ items: [], notifications: [], enabled: false, available: false }))
    return
  }
  if (route === '/api/gateway/snapshot') {
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify({ system: { '0/Dc/Battery/Soc': 61, '0/Ac/Grid/L1/Power': 123 }, battery: { '0/Soc': 61, '0/CustomName': 'Public fixture battery' } }))
    return
  }
  const file = path.resolve(dist, `.${route === '/' ? '/index.html' : route}`)
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404).end('Not found')
    return
  }
  res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream')
  fs.createReadStream(file).pipe(res)
})

async function eventually(read, check, message) {
  const deadline = Date.now() + 10000
  let last
  while (Date.now() < deadline) {
    last = await read()
    if (check(last)) return last
    await delay(50)
  }
  assert.fail(`${message}; last value: ${JSON.stringify(last)}`)
}

async function scenario(name, run) {
  try {
    await run()
    scenarios.push(name)
    console.log(`PASS ${name}`)
  } catch (error) {
    failures.push({ name, message: String(error.stack || error) })
    throw error
  }
}

function broadcast() {
  for (const ws of fixture.sockets) ws.send(JSON.stringify(fixture.state))
}

async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${server.address().port}`
  let browser, context, page
  try {
    browser = await playwright[engine].launch({ headless: true, ...(channel ? { channel } : {}) })
    context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en-US', serviceWorkers: 'block' })
    context.setDefaultTimeout(10000)
    context.setDefaultNavigationTimeout(15000)
    await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
    await context.route('**/*', route => {
      const url = new URL(route.request().url())
      if (url.origin === origin) return route.continue()
      blockedRequests.push(url.origin)
      return route.abort('blockedbyclient')
    })
    await context.routeWebSocket('**/*', ws => {
      const url = new URL(ws.url())
      if (url.host !== new URL(origin).host || url.pathname !== '/ws') {
        blockedRequests.push(url.origin)
        ws.close()
        return
      }
      if (fixture.paused) { ws.close(); return }
      fixture.connections++
      fixture.sockets.add(ws)
      ws.onClose(() => fixture.sockets.delete(ws))
      ws.onMessage(raw => {
        const command = JSON.parse(raw.toString())
        fixture.commands.push(command)
        if (fixture.reject) {
          ws.send(JSON.stringify({ type: 'command_error', action: command.action, request_id: command.request_id, error: 'Synthetic rejection' }))
          return
        }
        ws.send(JSON.stringify({ type: 'command_result', action: command.action, request_id: command.request_id, status: 'accepted' }))
        if (command.action === 'set_settings') {
          const { action: _action, request_id: _requestId, ...patch } = command
          Object.assign(fixture.state.ui_config.settings, patch)
          broadcast()
        }
      })
      ws.send(JSON.stringify(fixture.state))
    })
    page = await context.newPage()
    page.on('pageerror', error => browserErrors.push(String(error)))
    page.on('console', message => {
      if (message.type() === 'error') browserErrors.push(message.text())
    })
    await page.goto(origin, { waitUntil: 'networkidle' })

    await scenario('live-telemetry-load-order-and-real-chart', async () => {
      await eventually(() => page.getByTestId('active-load').allTextContents(), rows => rows.length === 3 && rows[0].includes('Fixture refrigerator') && rows[1].includes('-100W'), 'Real load rows should be ordered and preserve generation')
      assert.equal(await page.getByTestId('transport-status').getAttribute('data-connected'), 'true')
      await eventually(() => page.locator('canvas').count(), count => count > 0, 'ECharts should render an actual canvas')
      assert(await page.getByText('73%', { exact: true }).count() > 0)
    })
    await scenario('telemetry-updates-without-reload', async () => {
      fixture.state.battery_soc = 42
      fixture.state.loads['1'] = 333
      broadcast()
      await eventually(() => page.getByTestId('active-load').first().textContent(), text => text.includes('333W'), 'Live websocket update should update load')
      await page.getByText('42%', { exact: true }).first().waitFor()
    })
    await scenario('theme-persists-across-reload', async () => {
      await page.getByRole('button', { name: 'Switch to dark theme' }).click()
      assert.equal(await page.evaluate(() => localStorage.getItem('theme')), 'dark')
      await page.reload({ waitUntil: 'networkidle' })
      assert(await page.locator('html').evaluate(node => node.classList.contains('dark')))
      await page.getByRole('button', { name: 'Switch to light theme' }).click()
    })
    await scenario('native-settings-dialog-focus-and-escape', async () => {
      await page.getByRole('button', { name: 'Settings', exact: true }).focus()
      await page.keyboard.press('Enter')
      const dialog = page.getByRole('dialog', { name: 'Configuration' })
      await dialog.waitFor()
      assert(await dialog.evaluate(node => node.open && node.contains(document.activeElement)))
      await page.keyboard.press('Escape')
      await dialog.waitFor({ state: 'hidden' })
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Settings')
    })
    await scenario('invalid-settings-stay-local-before-valid-save', async () => {
      await page.getByRole('button', { name: 'Settings', exact: true }).click()
      const before = fixture.commands.length
      await page.getByLabel('MQTT port', { exact: true }).fill('70000')
      await page.getByRole('button', { name: 'Save Configuration' }).click()
      await page.getByRole('alert').filter({ hasText: 'MQTT port must' }).waitFor()
      assert.equal(fixture.commands.length, before, 'Invalid form must not send a command')
      await page.getByLabel('MQTT port', { exact: true }).fill('1884')
      await page.getByLabel('Camera topic', { exact: true }).fill('fixture/camera')
      await page.getByRole('button', { name: 'Save Configuration' }).click()
      await eventually(() => fixture.commands.at(-1), command => command?.action === 'set_settings' && command.mqtt_port === 1884 && command.camera_topic === 'fixture/camera', 'Save should send typed settings')
      await page.getByRole('button', { name: 'Close settings' }).click()
      await page.getByText('Command accepted by server.', { exact: false }).waitFor()
    })
    await scenario('saved-section-visibility-survives-reload', async () => {
      await page.getByRole('button', { name: 'Settings', exact: true }).click()
      await page.getByRole('group', { name: 'Dashboard sections' }).getByRole('checkbox', { name: 'Active loads', exact: true }).uncheck()
      await eventually(() => fixture.state.ui_config.settings.show_active_loads, value => value === false, 'Visibility update should reach the backend fixture')
      await page.getByRole('button', { name: 'Close settings' }).click()
      await page.getByTestId('active-loads').waitFor({ state: 'hidden' })
      await page.reload({ waitUntil: 'networkidle' })
      assert.equal(await page.getByTestId('active-loads').count(), 0)
      fixture.state.ui_config.settings.show_active_loads = true
      broadcast()
      await page.getByTestId('active-loads').waitFor()
    })
    await scenario('rejected-command-shows-error-without-changing-state', async () => {
      fixture.reject = true
      await page.getByRole('button', { name: 'Settings', exact: true }).click()
      await page.getByLabel('Camera topic', { exact: true }).fill('fixture/rejected')
      await page.getByRole('button', { name: 'Save Configuration' }).click()
      await page.getByRole('button', { name: 'Close settings' }).click()
      await page.getByText('The server rejected the command.', { exact: false }).waitFor()
      assert.equal(fixture.state.ui_config.settings.camera_topic, 'fixture/camera')
      fixture.reject = false
    })
    await scenario('disconnect-and-reconnect-restores-live-telemetry', async () => {
      fixture.paused = true
      for (const ws of fixture.sockets) ws.close()
      fixture.sockets.clear()
      await eventually(() => page.getByTestId('transport-status').getAttribute('data-connected'), value => value === 'false', 'Disconnected transport must be visible')
      fixture.paused = false
      const before = fixture.connections
      await page.evaluate(() => window.dispatchEvent(new Event('online')))
      await eventually(() => fixture.connections, count => count > before, 'Browser should reconnect')
      await eventually(() => page.getByTestId('transport-status').getAttribute('data-connected'), value => value === 'true', 'New telemetry should be live')
    })
    await scenario('mobile-layout-keeps-settings-keyboard-accessible', async () => {
      await page.setViewportSize({ width: 390, height: 844 })
      await page.getByRole('button', { name: 'Settings', exact: true }).focus()
      await page.keyboard.press('Enter')
      await page.getByRole('dialog', { name: 'Configuration' }).waitFor()
      await page.keyboard.press('Escape')
      await page.getByRole('dialog').waitFor({ state: 'hidden' })
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Mobile viewport must not overflow horizontally')
    })
    await scenario('public-mode-has-telemetry-but-no-write-controls', async () => {
      await page.addInitScript(() => { window.__INVERTER_PUBLIC__ = true })
      const before = fixture.commands.length
      await page.reload({ waitUntil: 'networkidle' })
      await page.getByText('PUBLIC · READ-ONLY', { exact: true }).waitFor()
      await page.getByText('61%', { exact: true }).first().waitFor()
      assert.equal(await page.getByRole('button', { name: 'Settings', exact: true }).count(), 0)
      assert.equal(await page.getByRole('button', { name: /DRY/ }).count(), 0)
      assert.equal(fixture.commands.length, before)
    })
    await scenario('no-browser-errors-or-external-requests', async () => {
      assert.deepEqual(browserErrors, [])
      assert.deepEqual(blockedRequests, [])
    })
    await page.screenshot({ path: path.join(diagnostics, 'final.png'), fullPage: true })
    const runtimeFile = path.join(diagnostics, 'runtime.json')
    const runtime = fs.existsSync(runtimeFile) ? JSON.parse(fs.readFileSync(runtimeFile, 'utf8')) : {
      schema: 1, target: 'local', channel, playwrightVersion: require(path.join(modulePath, 'package.json')).version,
      platform: process.platform, sourceSha: process.env.GITHUB_SHA || 'local', runId: process.env.GITHUB_RUN_ID || 'local', runAttempt: process.env.GITHUB_RUN_ATTEMPT || '1',
    }
    const candidateFile = path.join(diagnostics, 'candidate.json')
    const candidate = fs.existsSync(candidateFile) ? JSON.parse(fs.readFileSync(candidateFile, 'utf8')) : undefined
    if (candidate) assert.equal(candidate.sourceSha, runtime.sourceSha, 'Packaged candidate must match the tested source')
    fs.writeFileSync(evidence, `${JSON.stringify({ ...runtime, engine: browser.browserType().name(), browserVersion: browser.version(), testsPassed: scenarios.length, testsFailed: 0, scenarios, frontend, ...(candidate ? { candidate } : {}) }, null, 2)}\n`)
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(diagnostics, 'failure.png'), fullPage: true }).catch(() => {})
    throw error
  } finally {
    fs.writeFileSync(path.join(diagnostics, 'diagnostics.json'), `${JSON.stringify({ scenarios, failures, browserErrors, blockedRequests }, null, 2)}\n`)
    if (context) await context.tracing.stop({ path: path.join(diagnostics, 'trace.zip') }).catch(() => {})
    if (browser) await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
