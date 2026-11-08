/** Final real-fixture command journal checks shared by runtime QA and controls. */
const assert = require('node:assert/strict')

function expectedCommandJournal(transport, controller, home) {
  assert(['mqtt', 'igw'].includes(transport), 'Unexpected test transport')
  assert.equal(home.action, 'toggle')
  assert.equal(home.entity, 'switch.fixture_light')
  let publication
  if (transport === 'mqtt') {
    assert.equal(controller.action, 'setpoint')
    assert(Number.isFinite(controller.value), 'Expected an explicit test setpoint')
    publication = { transport, path: 'inverter/cmd/setpoint', body: { value: controller.value } }
  } else {
    assert.equal(controller.action, 'toggle')
    assert.equal(controller.entity, 'charge_battery')
    assert.equal(typeof controller.state, 'boolean')
    publication = { transport, path: '/v1/commands/toggle',
      body: { entity: controller.entity, state: controller.state ? 'on' : 'off' } }
  }
  return [publication, { transport: 'ha', path: '/api/services/switch/toggle',
    body: { entity_id: home.entity } }]
}

async function verifyFinalCommandJournal(readFixture, expectedCaptures, journalEpoch) {
  assert(Number.isSafeInteger(journalEpoch) && journalEpoch > 0, 'Seed must bind a positive journal epoch')
  const final = await readFixture()
  assert.equal(final.journalEpoch, journalEpoch, 'Fixture command journal was reset during the scenario')
  assert.deepEqual(final.capture, expectedCaptures,
    'Unexpected late, duplicate or unconfigured command in the final fixture journal')
}

// The real backends use Desktop's voltage-derived bank estimate: 51.2 V -> 78%.
// A device's reported 73% stays separate and renders with one decimal place.
const PACKAGED_BANK_SOC = 78
function assertPackagedTelemetry(value) {
  assert.equal(value.battery_voltage, 51.2, 'Fixture bank voltage must reach the real backend')
  assert.equal(value.battery_soc, PACKAGED_BANK_SOC, 'Bank SoC must use the Desktop voltage formula')
  const battery = value.batteries?.find(item => item.name === 'Fixture battery')
  assert(battery, 'Real native battery identity is missing')
  assert.equal(battery.soc, 73, 'Individual battery must preserve its device-reported SoC')
}

function failureState(value) {
  const result = {}
  for (const key of ['mqtt_connected', 'gateway_connected', 'controller_controls_available']) {
    if (typeof value?.[key] === 'boolean') result[key] = value[key]
  }
  for (const key of ['battery_soc', 'battery_voltage']) {
    if (Number.isFinite(value?.[key])) result[key] = value[key]
  }
  if (['mqtt', 'igw'].includes(value?.data_source)) result.data_source = value.data_source
  result.batterySoc = Array.isArray(value?.batteries)
    ? value.batteries.slice(0, 8).map(item => Number.isFinite(item?.soc) ? item.soc : null) : []
  return result
}

function failureFixture(value) {
  const result = { commandAttempts: Array.isArray(value?.capture) ? value.capture.length : null }
  if (Number.isSafeInteger(value?.journalEpoch)) result.journalEpoch = value.journalEpoch
  for (const key of ['generation', 'activeSessions', 'readySessions']) {
    if (Number.isSafeInteger(value?.mqtt?.[key])) result[key] = value.mqtt[key]
  }
  result.ready = value?.mqtt?.readySession != null
  if (Number.isSafeInteger(value?.haReads)) result.haReads = value.haReads
  return result
}

async function boundedDiagnostic(read, milliseconds = 1000) {
  let timer
  try {
    return await Promise.race([
      Promise.resolve().then(read).then(value => ({ status: 'available', value }),
        () => ({ status: 'unavailable' })),
      new Promise(resolve => { timer = setTimeout(() => resolve({ status: 'deadline' }), milliseconds) }),
    ])
  } finally {
    clearTimeout(timer)
  }
}


function visibleFailureDom(credential, root = document) {
  // Return a fixed numeric/boolean view only; never return the inspected text.
  const safeForScreenshot = !root.body.innerText.includes(credential) &&
    !Array.from(root.querySelectorAll('input, textarea')).some(element => element.value.includes(credential))
  return {
    safeForScreenshot,
    percentages: Array.from(root.querySelectorAll('div,span')).filter(element =>
      element.children.length === 0 && /^\d{1,3}(?:\.\d)?%$/.test(element.textContent.trim()) &&
      element.getClientRects().length > 0).slice(0, 12).map(element => element.textContent.trim()),
    connected: root.querySelector('[data-testid="transport-status"]')?.dataset.connected === 'true',
  }
}

function failureMessage(error, credential) {
  return String(error?.message || 'Packaged browser acceptance failed')
    .replaceAll(credential, '[redacted fixture credential]').slice(0, 2048)
}

function remainingWork(deadline, maximum = 30000) {
  const remaining = deadline - Date.now()
  assert(remaining > 0, 'Total packaged browser work deadline exceeded')
  return Math.min(maximum, remaining)
}

async function withinWorkDeadline(work, deadline) {
  let timer
  try {
    const milliseconds = remainingWork(deadline, 270000)
    const value = await Promise.race([
      Promise.resolve().then(work),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Total packaged browser work deadline exceeded')), milliseconds)
      }),
    ])
    remainingWork(deadline)
    return value
  } finally {
    clearTimeout(timer)
  }
}

function observeOwnedBrowser(server) {
  const child = server.process()
  assert(Number.isSafeInteger(child?.pid) && child.pid > 0, 'Browser server must expose its owned child process')
  let seenClose = false
  let resolveClosed
  const closed = new Promise(resolve => { resolveClosed = resolve })
  const onClose = () => { seenClose = true; resolveClosed() }
  child.once('close', onClose)
  return {
    closed,
    reaped: () => seenClose && (child.exitCode !== null || child.signalCode !== null),
    dispose: () => child.removeListener('close', onClose),
  }
}

async function stopOwnedBrowser(server, owned, budgets, status) {
  const graceful = await boundedDiagnostic(() => server.close(), budgets.close)
  status.close = graceful.status
  status.observedReap = owned.reaped()
  if (graceful.status === 'available' && status.observedReap) {
    status.reap = 'available'
    return 'closed'
  }
  // BrowserServer.kill is public and, in pinned Playwright, kills the owned
  // browser process group and waits for close/temp cleanup. A race alone is
  // never proof: also require the observed ChildProcess close and exit status.
  const killed = await boundedDiagnostic(() => server.kill(), budgets.kill)
  status.kill = killed.status
  const closed = await boundedDiagnostic(() => owned.closed, budgets.reap)
  status.reap = closed.status
  status.observedReap = owned.reaped()
  assert(killed.status === 'available' && closed.status === 'available' && status.observedReap,
    'Owned browser process did not terminate after bounded close and kill')
  return 'killed'
}

const BROWSER_CLEANUP_BUDGETS = Object.freeze({ diagnostics: 5000, close: 2000, kill: 3000, reap: 1000 })

async function withStartupDiagnostics(work, diagnostics, milliseconds = BROWSER_CLEANUP_BUDGETS.diagnostics) {
  try {
    return await work()
  } catch (error) {
    await boundedDiagnostic(diagnostics, milliseconds)
    throw error
  }
}

async function withOwnedBrowser(server, work, diagnostics,
  budgets = BROWSER_CLEANUP_BUDGETS, deadline = Date.now() + 270000) {
  const owned = observeOwnedBrowser(server)
  let result
  let failure
  let failed = false
  try {
    result = await withinWorkDeadline(work, deadline)
  } catch (error) {
    failed = true
    failure = error
    await boundedDiagnostic(diagnostics, budgets.diagnostics)
  }
  let cleanupFailure
  let cleanupFailed = false
  // Fixed status codes only: never copy errors, process args, URLs or credentials.
  const cleanupStatus = { close: 'not-attempted', kill: 'not-attempted', reap: 'not-observed', observedReap: false }
  try {
    await stopOwnedBrowser(server, owned, budgets, cleanupStatus)
  } catch (error) {
    cleanupFailed = true
    cleanupFailure = error
  } finally {
    owned.dispose()
  }
  if (failed) {
    if (cleanupFailed) console.error('Owned browser cleanup failed; original scenario failure retained', cleanupStatus)
    throw failure
  }
  if (cleanupFailed) throw cleanupFailure
  return result
}

module.exports = {
  expectedCommandJournal, verifyFinalCommandJournal, PACKAGED_BANK_SOC, assertPackagedTelemetry,
  failureState, failureFixture, boundedDiagnostic, visibleFailureDom, failureMessage, withStartupDiagnostics,
  withOwnedBrowser, remainingWork, withinWorkDeadline,
}
