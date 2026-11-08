#!/usr/bin/env node
/** Real HTTP/WebSocket acceptance against an isolated, exact packaged backend. */
const workDeadline = Date.now() + 270000 // One total budget; reserve 30s inside the unchanged parent 300s limit.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { setTimeout: delay } = require('node:timers/promises')
const { workspacePath } = require('./pwa_acceptance.cjs')
const { expectedCommandJournal, verifyFinalCommandJournal, PACKAGED_BANK_SOC, assertPackagedTelemetry,
  failureState, failureFixture, boundedDiagnostic, visibleFailureDom, failureMessage, withOwnedBrowser, remainingWork, withinWorkDeadline } = require('./packaged_browser_contract.cjs')
const { waitForHydratedDashboard } = require('./browser-readiness.cjs')
const [base, fixture, transport, outputArgument, ociSha256, backend] = process.argv.slice(2)
const output = workspacePath(outputArgument)
assert.equal(path.extname(output), '.json', 'Runtime evidence must be a JSON file')
assert(!fs.existsSync(output), 'Runtime evidence output must be new')
for (const address of [base, fixture]) {
  const url = new URL(address)
  assert.equal(url.hostname, '127.0.0.1', 'Packaged QA may only contact loopback fixtures')
  assert.equal(url.protocol, 'http:')
  assert(url.port && url.pathname === '/' && !url.username && !url.password)
}
assert(['mqtt', 'igw'].includes(transport))
assert(['python', 'go'].includes(backend))
const token = process.env.CI_PACKAGED_BROWSER_TOKEN
assert.match(token || '', /^[A-Za-z0-9_-]{43,128}$/, 'A bounded ephemeral fixture credential is required')
const modulePath = process.env.CI_PLAYWRIGHT_MODULE
assert(modulePath && path.isAbsolute(modulePath), 'Explicit isolated Playwright module required')
const playwright = require(modulePath)
assert.match(ociSha256, /^[0-9a-f]{64}$/)
const producer = { repository: process.env.GITHUB_REPOSITORY, source_sha: process.env.GITHUB_SHA,
  run_id: Number(process.env.GITHUB_RUN_ID), run_attempt: Number(process.env.GITHUB_RUN_ATTEMPT) }
const runs = []

async function request(address, route, options = {}) {
  const response = await fetch(address + route, { signal: AbortSignal.timeout(5000), redirect: 'error', ...options })
  assert(response.ok, `Fixture/runtime request failed: ${route} (${response.status})`)
  return response.json()
}
const state = () => request(base, '/api/state', { headers: { Authorization: `Bearer ${token}` } })
const fixtureState = () => request(fixture, '/fixture/status')
const control = (action, body) => request(fixture, '/fixture/' + action, { method: 'POST', body: JSON.stringify(body) })
async function until(read, predicate, label) {
  const deadline = Math.min(Date.now() + 25000, workDeadline)
  async function poll() {
    if (Date.now() >= deadline) throw new Error(`Timed out: ${label}`)
    const value = await read()
    if (predicate(value)) return value
    await delay(150)
    return poll()
  }
  return poll()
}

async function subscribedSession(generation) {
  const status = await until(fixtureState, value => value.mqtt?.readySession &&
    (generation === undefined || value.mqtt.generation === generation), 'current MQTT subscriptions')
  return status.mqtt.readySession
}

function liveAcknowledgement(response, binding, publications) {
  assert.equal(response.ok, true)
  assert.equal(response.generation, binding.generation)
  assert.equal(response.sessionId, binding.sessionId)
  assert.equal(response.livePublications, publications, 'Fixture must acknowledge every live publication')
}

async function saveFailureDiagnostics(page, engine, stage) {
  // API state collection is independent of renderer health. No raw state,
  // URLs, headers, query credentials, exception text or arbitrary DOM is saved.
  const [source, peer, dom] = await Promise.all([
    boundedDiagnostic(async () => failureState(await state())),
    boundedDiagnostic(async () => failureFixture(await fixtureState())),
    page ? boundedDiagnostic(() => page.evaluate(visibleFailureDom, token)) : { status: 'unavailable' },
  ])
  let screenshot = { status: 'unavailable' }
  if (dom.status === 'available' && dom.value.safeForScreenshot === true) {
    screenshot = await boundedDiagnostic(async () => {
      await page.screenshot({ path: workspacePath(output.replace(/\.json$/, `-${engine}.failure.png`)),
        timeout: 1000, mask: [page.locator('input, textarea'), page.getByText(token, { exact: false })] })
      return 'saved'
    })
  }
  // Ownership cleanup follows immediately, including when no page was created
  // or a renderer exceeded its independent collection deadline.
  fs.writeFileSync(workspacePath(output.replace(/\.json$/, `-${engine}.failure.json`)),
    JSON.stringify({ engine, transport, stage, source, peer, dom, screenshot }, null, 2) + '\n', { flag: 'wx' })
}

async function command(page, value) {
  return page.evaluate(({ base, token, value }) => new Promise((resolve, reject) => {
    const socket = new WebSocket(base.replace('http:', 'ws:') + '/ws?token=' + token)
    const timer = setTimeout(() => { socket.close(); reject(new Error('command deadline')) }, 8000)
    socket.onopen = () => socket.send(JSON.stringify(value))
    socket.onmessage = event => {
      const frame = JSON.parse(event.data)
      if (frame.request_id === value.request_id) {
        clearTimeout(timer)
        socket.close()
        resolve(frame)
      }
    }
    socket.onerror = () => { clearTimeout(timer); reject(new Error('command socket error')) }
  }), { base, token, value })
}

async function rejectedSocket(page, credential) {
  const opened = await page.evaluate(({ base, credential }) => new Promise((resolve, reject) => {
    const socket = new WebSocket(base.replace('http:', 'ws:') + '/ws?token=' + credential)
    const timer = setTimeout(() => { socket.close(); reject(new Error('rejection deadline')) }, 5000)
    socket.onopen = () => { clearTimeout(timer); socket.close(); resolve(true) }
    socket.onerror = socket.onclose = () => { clearTimeout(timer); resolve(false) }
  }), { base, credential })
  assert.equal(opened, false, 'Unauthorized or cross-origin WebSocket opened')
}

async function run(engine) {
  const session = await withinWorkDeadline(async () => {
    const binding = transport === 'mqtt' ? await subscribedSession() : {}
    const seeded = await control('seed', { transport, ...binding })
    if (transport === 'mqtt') liveAcknowledgement(seeded, binding, 16)
    return binding
  }, workDeadline)
  const server = await playwright[engine].launchServer({ headless: true, host: '127.0.0.1', timeout: remainingWork(workDeadline) })
  let page
  let stage = 'browser-connect'
  await withOwnedBrowser(server, async () => {
    const browser = await playwright[engine].connect(server.wsEndpoint(), { timeout: remainingWork(workDeadline, 10000) })
    stage = 'browser-context-and-routes'
    const context = await browser.newContext({ locale: 'en-US', viewport: { width: 1280, height: 900 }, serviceWorkers: 'block' })
    const unexpected = []
    await context.route('**/*', route => {
      const address = new URL(route.request().url())
      if ([base, fixture].includes(address.origin)) return route.continue()
      unexpected.push(address.origin)
      return route.abort('blockedbyclient')
    })
    await context.routeWebSocket('**/*', route => {
      const address = new URL(route.url())
      if (address.origin === base.replace('http:', 'ws:') && address.pathname === '/ws') {
        route.connectToServer()
        return
      }
      unexpected.push(address.origin)
      route.close({ code: 1008, reason: 'Only isolated backend WebSockets allowed' })
    })
    page = await context.newPage()
    const scenarios = []
    stage = 'http-auth'
    // Route guard only blocks external traffic; all permitted HTTP/WS uses production backend code.
    const response = await fetch(base + '/api/state', { signal: AbortSignal.timeout(5000), redirect: 'error' })
    if (backend === 'python') {
      assert.equal(response.status, 200, 'Python health-only endpoint remains available')
      const health = await response.json()
      const fields = new Set(['ok', 'dashboard_version', 'control_version', 'has_mqtt_state', 'data_source',
        'mqtt_connected', 'mqtt_reconnects', 'gateway_connected', 'gateway_polls', 'gateway_errors', 'gateway_url'])
      assert(Object.keys(health).every(key => fields.has(key)), 'Unauthenticated health response leaked live state')
      assert.equal(health.ok, true)
    } else {
      assert([401, 403].includes(response.status), 'Unauthenticated Go state endpoint must reject')
    }
    const protectedSettings = await fetch(base + '/api/settings', { signal: AbortSignal.timeout(5000), redirect: 'error' })
    assert([401, 403].includes(protectedSettings.status), 'Protected settings require authorization')
    stage = 'backend-telemetry'
    await page.goto(`${base}/?token=${token}`, { waitUntil: 'domcontentloaded' })
    const telemetry = await until(state, value => value.battery_soc === PACKAGED_BANK_SOC &&
      value.batteries?.some(item => item.name === 'Fixture battery' && item.soc === 73), 'real projected bank and battery telemetry')
    assertPackagedTelemetry(telemetry)
    stage = 'visible-telemetry'
    await waitForHydratedDashboard(page, PACKAGED_BANK_SOC)
    await page.getByText('73.0%', { exact: true }).waitFor({ state: 'visible' })
    await rejectedSocket(page, 'wrong-fixture-token')
    scenarios.push('real-http-auth-and-websocket-token-rejection')

    stage = 'source-authority'
    const actual = await until(state, value => value.data_source === transport &&
      (value.mqtt_connected || value.gateway_connected) && value.controller_controls_available === true,
    'real source and controller authority')
    assert(actual.dashboard_version, 'Packaged application must expose its actual version')
    stage = 'native-banner'
    const current = await until(state, value => value.notifications?.some(item => item.title === 'Fixture old alarm'), 'real native notification')
    const alarm = current.notifications.find(item => item.title === 'Fixture old alarm')
    const source = await fixtureState()
    assert.equal(Math.floor(Date.parse(alarm.ts) / 1000), source.eventEpoch)
    await page.getByText('Fixture old alarm', { exact: true }).waitFor()
    const banner = page.getByText('Fixture old alarm', { exact: true }).locator('..')
    assert(!/just now/i.test(await banner.innerText()), 'Source-aged alarm was reset to arrival time')
    assert.equal(Math.floor(Date.parse(await banner.locator('time').getAttribute('datetime')) / 1000), source.eventEpoch)
    assert((await banner.locator('time').innerText()).trim().length > 0, 'Banner needs a visible source age')
    scenarios.push('real-transport-native-banner-preserves-source-time')

    const controllerInput = transport === 'mqtt'
      ? { action: 'setpoint', value: 123, request_id: `accepted-${engine}` }
      : { action: 'toggle', entity: 'charge_battery', state: true, request_id: `accepted-${engine}` }
    const homeInput = { action: 'toggle', entity: 'switch.fixture_light', request_id: `home-${engine}` }
    const expectedJournal = expectedCommandJournal(transport, controllerInput, homeInput)
    await verifyFinalCommandJournal(fixtureState, [])
    const rejected = await command(page, { action: 'toggle', entity: 'switch.unconfigured', request_id: `unknown-${engine}` })
    assert.equal(rejected.type, 'command_error')
    await delay(200)
    await verifyFinalCommandJournal(fixtureState, [])
    scenarios.push('unknown-home-target-rejected-without-publication')

    const accepted = await command(page, controllerInput)
    assert.equal(accepted.type, 'command_result')
    assert.equal(accepted.status, 'accepted')
    await until(fixtureState, value => value.capture.length >= 1, 'exact fixture command')
    await verifyFinalCommandJournal(fixtureState, expectedJournal.slice(0, 1))
    scenarios.push('real-correlated-command-routes-once-to-selected-transport')

    stage = 'command-routing'
    await until(fixtureState, value => value.haReads > 0, 'real HA polling')
    await page.getByText('Fixture light', { exact: true }).waitFor()
    const home = await command(page, homeInput)
    assert.equal(home.type, 'command_result')
    assert.equal(home.status, 'accepted')
    await until(fixtureState, value => value.capture.some(item => item.transport === 'ha'), 'real HA service')
    await verifyFinalCommandJournal(fixtureState, expectedJournal)
    scenarios.push('configured-home-only-real-ha-service-route')

    const hostile = await context.newPage()
    await hostile.goto(fixture + '/fixture/status')
    await rejectedSocket(hostile, token)
    await hostile.close()
    scenarios.push('cross-origin-websocket-rejected-even-with-valid-token')

    if (transport === 'mqtt') {
      stage = 'mqtt-reconnect'
      const retired = await control('reconnect', { transport, ...session })
      await until(state, value => value.mqtt_connected === true && !value.controller_controls_available &&
        !value.notifications?.some(item => item.source === 'victron'), 'reconnected source clears authority and alarms')
      const nextSession = await subscribedSession(retired.generation)
      assert.notEqual(nextSession.sessionId, session.sessionId)
      const freshAck = await control('fresh-slot', { transport, ...nextSession })
      liveAcknowledgement(freshAck, nextSession, 1)
      const fresh = await until(state, value => value.notifications?.some(item => item.title === 'Fixture fresh alarm'), 'new generation alarm')
      assert(!fresh.notifications.find(item => item.title === 'Fixture fresh alarm').ts)
      await verifyFinalCommandJournal(fixtureState, expectedJournal)
      const stale = await command(page, { action: 'setpoint', value: 123, request_id: `retired-${engine}` })
      assert.equal(stale.type, 'command_error')
      await verifyFinalCommandJournal(fixtureState, expectedJournal)
      scenarios.push('reconnect-retires-authority-and-source-event-time')
    }
    assert.deepEqual(unexpected, [], 'Packaged browser attempted an external destination')
    await page.screenshot({ path: output.replace(/\.json$/, `-${engine}.png`) })
    // Re-read after every later HA/reconnect scenario: earlier routes-once proof
    // must not hide a duplicate or new command delivered later in this run.
    await verifyFinalCommandJournal(fixtureState, expectedJournal)
    runs.push({ engine, browserVersion: browser.version(), transport, status: 'passed',
      scenarios, producer, ociSha256, testsPassed: scenarios.length, testsFailed: 0, backendVersion: actual.dashboard_version })
  }, () => saveFailureDiagnostics(page, engine, stage), undefined, workDeadline)
}

;(async () => {
  // Each engine mutates the same synthetic peer; serialize its complete reset/run/cleanup.
  await ['chromium', 'firefox', 'webkit'].reduce((previous, engine) => previous.then(() => run(engine)), Promise.resolve())
  remainingWork(workDeadline)
  fs.writeFileSync(output, JSON.stringify({ runs }, null, 2) + '\n', { flag: 'wx' })
})().catch(error => { console.error(failureMessage(error, token)); process.exitCode = 1 })
