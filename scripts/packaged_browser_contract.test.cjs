/** Exercise the final command checker through real isolated loopback requests. */
const assert = require('node:assert/strict')
const http = require('node:http')
const { spawn } = require('node:child_process')
const { once } = require('node:events')
const test = require('node:test')
const { expectedCommandJournal, verifyFinalCommandJournal, assertPackagedTelemetry,
  failureState, failureFixture, boundedDiagnostic, visibleFailureDom, failureMessage, withStartupDiagnostics,
  withOwnedBrowser, remainingWork, withinWorkDeadline } = require('./packaged_browser_contract.cjs')

async function journalFixture(t) {
  const capture = []
  let journalEpoch = 0
  const seedRoute = '/fixture/seed'
  const routes = new Map([['/v1/commands/toggle', 'igw'], ['/api/services/switch/toggle', 'ha']])
  const server = http.createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json')
    if (request.method === 'GET' && request.url === '/fixture/status') {
      response.end(JSON.stringify({ capture, journalEpoch }))
      return
    }
    if (request.method !== 'POST' || (!routes.has(request.url) && request.url !== seedRoute)) {
      response.writeHead(404).end('{}')
      return
    }
    try {
      let body = ''
      for await (const chunk of request) {
        body += chunk.toString('utf8')
        assert(body.length <= 4096, 'Fixture request exceeds its bound')
      }
      const value = JSON.parse(body)
      if (request.url === seedRoute) {
        assert.deepEqual(value, { transport: 'igw' })
        journalEpoch++
        capture.length = 0
        response.end(JSON.stringify({ journalEpoch }))
        return
      }
      capture.push({ transport: routes.get(request.url), path: request.url, body: value })
      response.end('{}')
    } catch {
      response.writeHead(400).end('{}')
    }
  })
  server.requestTimeout = 2000
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  t.after(async () => {
    await new Promise((resolve, reject) => {
      server.close(error => error ? reject(error) : resolve())
      server.closeAllConnections()
    })
  })
  const origin = `http://127.0.0.1:${server.address().port}`
  const publish = async (route, body) => {
    const response = await fetch(origin + route, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(2000),
    })
    assert.equal(response.status, 200)
    return response.json()
  }
  const read = async () => {
    const response = await fetch(origin + '/fixture/status', { signal: AbortSignal.timeout(2000) })
    assert.equal(response.status, 200)
    return response.json()
  }
  const seed = async () => (await publish(seedRoute, { transport: 'igw' })).journalEpoch
  return { publish, read, seed }
}

const controllerInput = { action: 'toggle', entity: 'charge_battery', state: true }
const homeInput = { action: 'toggle', entity: 'switch.fixture_light' }

async function publishAllowed(publish) {
  await publish('/v1/commands/toggle', { entity: 'charge_battery', state: 'on' })
  await publish('/api/services/switch/toggle', { entity_id: 'switch.fixture_light' })
}

test('final journal rejects a duplicate published after the initial check', { timeout: 10000 }, async t => {
  const { publish, read, seed } = await journalFixture(t)
  const epoch = await seed()
  const expected = expectedCommandJournal('igw', controllerInput, homeInput)
  await publishAllowed(publish)
  await verifyFinalCommandJournal(read, expected, epoch)
  // The initial routes-once check has passed. A later duplicate must invalidate it.
  await publish('/v1/commands/toggle', { entity: 'charge_battery', state: 'on' })
  await assert.rejects(verifyFinalCommandJournal(read, expected, epoch), /Unexpected late, duplicate/)
  assert.equal((await read()).capture.length, 3)
})

test('unsolicited command before the first capture cannot become an expected baseline', { timeout: 10000 }, async t => {
  const { publish, read, seed } = await journalFixture(t)
  const epoch = await seed()
  const expected = expectedCommandJournal('igw', controllerInput, homeInput)
  await publish('/v1/commands/toggle', { entity: 'charge_battery', state: 'off' })
  await publishAllowed(publish)
  // No journal has been read yet. Expected publications come only from test inputs.
  await assert.rejects(verifyFinalCommandJournal(read, expected, epoch), /Unexpected late, duplicate/)
  assert.equal((await read()).capture.length, 3)
})

test('forbidden command followed by an IGW reset cannot pass as an empty journal', { timeout: 10000 }, async t => {
  const { publish, read, seed } = await journalFixture(t)
  const originalEpoch = await seed()
  await verifyFinalCommandJournal(read, [], originalEpoch)
  await publish('/api/services/switch/toggle', { entity_id: 'switch.unconfigured' })
  assert.equal((await read()).capture.length, 1)
  const resetEpoch = await seed()
  assert(resetEpoch > originalEpoch)
  assert.deepEqual((await read()).capture, [])
  await assert.rejects(verifyFinalCommandJournal(read, [], originalEpoch), /journal was reset/)
  await assert.rejects(verifyFinalCommandJournal(read, [], undefined), /positive journal epoch/)
})

test('expected MQTT and HA publications use explicit sent inputs and fixed routes', () => {
  assert.deepEqual(expectedCommandJournal('mqtt', { action: 'setpoint', value: 317 }, homeInput), [
    { transport: 'mqtt', path: 'inverter/cmd/setpoint', body: { value: 317 } },
    { transport: 'ha', path: '/api/services/switch/toggle', body: { entity_id: 'switch.fixture_light' } },
  ])
  assert.throws(() => expectedCommandJournal('other', controllerInput, homeInput), /Unexpected test transport/)
})


test('packaged telemetry distinguishes the voltage bank estimate from reported battery SoC', () => {
  const value = { battery_voltage: 51.2, battery_soc: 78, batteries: [{ name: 'Fixture battery', soc: 73 }] }
  assertPackagedTelemetry(value)
  assert.throws(() => assertPackagedTelemetry({ ...value, battery_soc: 73 }), /voltage formula/)
  assert.throws(() => assertPackagedTelemetry({ ...value, batteries: [{ name: 'Fixture battery', soc: 78 }] }), /device-reported/)
})

test('failure projections cannot persist credentials or arbitrary state strings', () => {
  const secret = 'sentinel-credential-that-must-not-be-exported'
  const state = failureState({ battery_soc: 78, battery_voltage: 51.2, data_source: 'mqtt', mqtt_connected: true,
    dashboard_version: secret, token: secret, gateway_url: secret, current_state: secret,
    batteries: [{ name: secret, soc: 73, token: secret }, { soc: secret }], controller_controls_available: secret })
  assert.deepEqual(state, { mqtt_connected: true, battery_soc: 78, battery_voltage: 51.2,
    data_source: 'mqtt', batterySoc: [73, null] })
  const fixture = failureFixture({ journalEpoch: 3, mqtt: { generation: 2, activeSessions: 1, readySessions: 1,
    readySession: { token: secret } }, capture: [{ body: { token: secret } }], haReads: 3, token: secret })
  assert.equal(fixture.commandAttempts, 1)
  assert.equal(fixture.journalEpoch, 3)
  assert(!Object.hasOwn(failureFixture({ journalEpoch: secret }), 'journalEpoch'))
  assert(!JSON.stringify({ state, fixture }).includes(secret))
})

test('blocked or throwing diagnostic collectors have a finite host deadline and omit errors', { timeout: 2000 }, async () => {
  const start = Date.now()
  assert.deepEqual(await boundedDiagnostic(() => new Promise(() => {}), 25), { status: 'deadline' })
  assert(Date.now() - start < 1000)
  const error = new Error('secret-in-error-text')
  assert.deepEqual(await boundedDiagnostic(() => { throw error }), { status: 'unavailable' })
  assert.deepEqual(await boundedDiagnostic(() => 78), { status: 'available', value: 78 })
})


test('visible credential or unqualified DOM cannot authorize a failure screenshot', () => {
  const credential = 'ephemeral-nonce-never-written'
  const number = { children: [], textContent: '78%', getClientRects: () => [1] }
  const root = { body: { innerText: '78% ' + credential },
    querySelectorAll: selector => selector === 'div,span' ? [number] : [], querySelector: () => null }
  const blocked = visibleFailureDom(credential, root)
  assert.equal(blocked.safeForScreenshot, false)
  assert(!JSON.stringify(blocked).includes(credential))
  root.body.innerText = '78%'
  assert.equal(visibleFailureDom(credential, root).safeForScreenshot, true)
  root.querySelector = () => ({ dataset: { connected: 'true' } })
  assert.equal(visibleFailureDom(credential, root).connected, true)
  root.querySelector = () => ({ dataset: { connected: 'false' } })
  assert.equal(visibleFailureDom(credential, root).connected, false)
  root.querySelectorAll = selector => selector === 'div,span' ? [number] : [{ value: credential }]
  assert.equal(visibleFailureDom(credential, root).safeForScreenshot, false)
})


async function ownedChild(t) {
  // Fixed Node executable and fixed program: no shell, user args or external I/O.
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: ['ignore', 'pipe', 'pipe'] })
  const closed = once(child, 'close')
  await once(child, 'spawn')
  const kill = async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
    const reaped = await boundedDiagnostic(() => closed, 1000)
    assert.equal(reaped.status, 'available', 'Test-owned child did not close after SIGKILL')
  }
  t.after(kill)
  return { child, kill }
}

const cleanupBudgets = { diagnostics: 25, close: 25, kill: 1000, reap: 250 }

for (const stage of ['mqtt-subscriptions', 'fixture-seed', 'browser-launch']) {
  test(`${stage} startup failure collects one API-only diagnostic and retains its error`, async () => {
    const credential = 'ephemeral-startup-credential'
    const original = new Error(credential)
    const saved = []
    await assert.rejects(withStartupDiagnostics(async () => { throw original }, async () => {
      saved.push({ stage, source: failureState({ battery_soc: 78, token: credential }),
        peer: failureFixture({ mqtt: { activeSessions: 0 }, token: credential }) })
    }), error => error === original)
    assert.equal(saved.length, 1)
    assert.equal(saved[0].stage, stage)
    assert(!JSON.stringify(saved).includes(credential))
  })
}

test('startup diagnostics are bounded and successful startup does not duplicate failure evidence', { timeout: 2000 }, async () => {
  const original = new Error('Original startup failure')
  await assert.rejects(withStartupDiagnostics(async () => { throw original }, () => new Promise(() => {}), 25),
    error => error === original)
  const launched = { server: 'owned only after successful launch' }
  let diagnostics = 0
  assert.equal(await withStartupDiagnostics(async () => launched, async () => { diagnostics++ }), launched)
  assert.equal(diagnostics, 0)
})

test('hung graceful close falls back to actual owned-child kill and observed reap', { timeout: 3000 }, async t => {
  const { child, kill } = await ownedChild(t)
  let kills = 0
  const server = { process: () => child, close: () => new Promise(() => {}),
    kill: async () => { kills++; await kill() } }
  assert.equal(await withOwnedBrowser(server, async () => 'completed', async () => {}, cleanupBudgets), 'completed')
  assert.equal(kills, 1)
  assert.equal(child.signalCode, 'SIGKILL')
  assert.throws(() => process.kill(child.pid, 0), { code: 'ESRCH' })
})

test('a fulfilled close without process exit is not cleanup proof', { timeout: 3000 }, async t => {
  const { child, kill } = await ownedChild(t)
  let kills = 0
  const server = { process: () => child, close: async () => {}, kill: async () => { kills++; await kill() } }
  await withOwnedBrowser(server, async () => {}, async () => {}, cleanupBudgets)
  assert.equal(kills, 1)
  assert.equal(child.signalCode, 'SIGKILL')
})

// Node executes these independent top-level tests sequentially by default.
for (const stage of ['connect', 'newContext', 'route', 'routeWebSocket', 'newPage']) {
  test(`${stage} failure retains the original error and reaps its owned child`, { timeout: 3000 }, async t => {
    const { child, kill } = await ownedChild(t)
    const original = new Error(stage + ': injected-ephemeral-fixture-nonce')
    const server = { process: () => child, close: () => new Promise(() => {}), kill }
    await assert.rejects(withOwnedBrowser(server,
      async () => { throw original }, () => new Promise(() => {}), cleanupBudgets), error => error === original)
    assert.equal(child.signalCode, 'SIGKILL')
    assert(!failureMessage(original, 'injected-ephemeral-fixture-nonce').includes('injected-ephemeral-fixture-nonce'))
  })
}

test('a cleanup failure preserves an earlier scenario error and remains a failed qualification', { timeout: 3000 }, async t => {
  const { child } = await ownedChild(t)
  const original = new Error('Original scenario failure')
  const warning = t.mock.method(console, 'error', () => {})
  const server = { process: () => child, close: async () => {}, kill: async () => {} }
  await assert.rejects(withOwnedBrowser(server, async () => { throw original }, async () => {}, cleanupBudgets),
    error => error === original)
  assert.equal(warning.mock.calls.length, 1)
  assert.deepEqual(warning.mock.calls[0].arguments,
    ['Owned browser cleanup failed; original scenario failure retained',
      { close: 'available', kill: 'available', reap: 'deadline', observedReap: false }])
  assert.equal(child.exitCode, null)
  assert.equal(child.signalCode, null)
  // The test-owned after hook must reap the deliberately failed-cleanup child.
})

test('failed close and kill expose fixed statuses without exporting exception credentials', { timeout: 3000 }, async t => {
  const { child } = await ownedChild(t)
  const credential = 'ephemeral-cleanup-credential'
  const original = new Error('Original scenario ' + credential)
  const warning = t.mock.method(console, 'error', () => {})
  const server = { process: () => child,
    close: async () => { throw new Error(credential) },
    kill: async () => { throw new Error(credential) } }
  await assert.rejects(withOwnedBrowser(server, async () => { throw original }, async () => {}, cleanupBudgets),
    error => error === original)
  assert.equal(warning.mock.calls.length, 1)
  assert.deepEqual(warning.mock.calls[0].arguments[1],
    { close: 'unavailable', kill: 'unavailable', reap: 'deadline', observedReap: false })
  assert(!JSON.stringify(warning.mock.calls[0].arguments).includes(credential))
})

test('failed kill or missing reap cannot produce a successful qualification', { timeout: 3000 }, async t => {
  const { child } = await ownedChild(t)
  const server = { process: () => child, close: async () => {}, kill: async () => {} }
  await assert.rejects(withOwnedBrowser(server, async () => {}, async () => {}, cleanupBudgets), /did not terminate/)
  assert.equal(child.exitCode, null)
  assert.equal(child.signalCode, null)
  // Test-owned after hook now terminates the deliberately unreaped negative control.
})


test('one absolute work deadline expires before owned cleanup, without renewing per stage', { timeout: 3000 }, async t => {
  const { child, kill } = await ownedChild(t)
  const server = { process: () => child, close: () => new Promise(() => {}), kill }
  const deadline = Date.now() + 25
  assert(remainingWork(deadline) <= 25)
  await assert.rejects(withOwnedBrowser(server, () => new Promise(() => {}), async () => {},
    cleanupBudgets, deadline), /Total packaged browser work deadline/)
  assert.equal(child.signalCode, 'SIGKILL')
  assert.throws(() => remainingWork(deadline), /deadline/)
  await assert.rejects(withinWorkDeadline(async () => 'late success', deadline), /deadline/)
})
