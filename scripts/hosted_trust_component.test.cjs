const assert = require('node:assert/strict')
const { test } = require('node:test')
const { EventEmitter } = require('node:events')
const { PassThrough } = require('node:stream')
const fs = require('node:fs')
const path = require('node:path')
const { requireHostedContext, invoke, safeStatus, observation, lifecycle, componentPassed, prepareStep, CHECKOUT, HOSTED_TEMP, REPOSITORY, WORKFLOW, WORKFLOW_FILE } = require('./hosted_trust_component.cjs')

const FINGERPRINT = 'A'.repeat(64)
const SOURCE = 'b'.repeat(40)
const fixture = { directory: '/synthetic-owned-fixture', cert: '/synthetic-owned-fixture/cert.pem', fingerprint: FINGERPRINT }
const good = () => ({ ok: true, code: null, exitStatus: 0, signal: null, reaped: true, output: Buffer.alloc(0) })
const context = () => ({ CI: 'true', GITHUB_ACTIONS: 'true', GITHUB_EVENT_NAME: 'pull_request', GITHUB_JOB: 'trust-lifecycle', GITHUB_REPOSITORY: REPOSITORY, GITHUB_WORKFLOW: WORKFLOW, RUNNER_OS: 'macOS', RUNNER_ENVIRONMENT: 'github-hosted', TRUST_COMPONENT_MODE: 'admin-trust-lifecycle-v1', RUNNER_TEMP: HOSTED_TEMP, GITHUB_WORKSPACE: CHECKOUT, TRUST_COMPONENT_SOURCE_SHA: SOURCE, GITHUB_RUN_ID: '1234', GITHUB_RUN_ATTEMPT: '1', GITHUB_WORKFLOW_REF: `${REPOSITORY}/${WORKFLOW_FILE}@refs/pull/42/merge` })
const event = () => ({ number: 42, repository: { full_name: REPOSITORY }, pull_request: { head: { sha: SOURCE, repo: { full_name: REPOSITORY } }, base: { repo: { full_name: REPOSITORY } } } })
const report = () => ({ operations: [], observations: [] })
const right = () => ({ osStatus: 0, class: 'rule', allowRoot: null, ruleCount: 1, hasIsRootRule: true })
function measured(present, count) {
  return { fingerprint256: FINGERPRINT, uid: 501, effectiveUid: 501, adminStatus: count === 0 ? -25263 : 0, adminCount: count, targetStatus: present ? 0 : -25300, targetPresent: present, sipUnrestrictedFsCheck: 0, rights: { 'com.apple.trust-settings.admin': right(), 'config.modify.trust-settings': right() } }
}
function fakeLifecycle({ removeFails = false, addFails = false, observerFailsAt = -1, throwingRemove = false, retainedTrust = false } = {}) {
  const calls = [], states = [measured(false, 0), measured(true, 1), measured(retainedTrust, retainedTrust ? 1 : 0), measured(retainedTrust, retainedTrust ? 1 : 0)]
  let reads = 0
  const run = async (command, args, env, timeoutMs) => {
    calls.push({ command, args, env, timeoutMs })
    if (command.endsWith('/observer')) {
      const index = reads++
      if (index === observerFailsAt) return { ...good(), ok: false, exitStatus: 17, output: Buffer.from('raw observer secret must not escape') }
      return { ...good(), output: Buffer.from(JSON.stringify(states[index])) }
    }
    if (args[1] === 'remove-trusted-cert' && throwingRemove) throw new Error('raw cleanup secret must not escape')
    if ((args[1] === 'remove-trusted-cert' && removeFails) || (args[1] === 'add-trusted-cert' && addFails)) return { ...good(), ok: false, code: 'ETIMEDOUT', exitStatus: null, signal: 'SIGTERM' }
    return good()
  }
  return { calls, run }
}

test('only exact same-repository hosted PR context with explicit mode is admitted', () => {
  assert.equal(requireHostedContext(context(), event(), 'darwin').sourceSha, SOURCE)
  for (const [key, value] of [['CI', 'false'], ['GITHUB_EVENT_NAME', 'workflow_dispatch'], ['RUNNER_ENVIRONMENT', 'self-hosted'], ['TRUST_COMPONENT_MODE', ''], ['GITHUB_WORKFLOW_REF', `${REPOSITORY}/other.yml@refs/pull/42/merge`], ['TRUST_COMPONENT_SOURCE_SHA', 'c'.repeat(40)]]) {
    const env = { ...context(), [key]: value }
    assert.throws(() => requireHostedContext(env, event(), 'darwin'))
  }
  const fork = event(); fork.pull_request.head.repo.full_name = 'foreign/fork'
  assert.throws(() => requireHostedContext(context(), fork, 'darwin'))
  assert.throws(() => requireHostedContext(context(), event(), 'linux'))
})

test('local executable guard precedes all process and trust actions', () => {
  const source = fs.readFileSync(path.join(__dirname, 'hosted_trust_component.cjs'), 'utf8')
  assert(source.indexOf('const { identity, childEnv } = executionContext()') < source.indexOf("const head = await invoke('/usr/bin/git'"))
  assert(!source.includes('process.argv'))
  const { executionContext } = require('./hosted_trust_component.cjs')
  assert.throws(() => executionContext({ GITHUB_ACTIONS: 'false' }))
})

test('every exact environment guard rejects missing or mismatched admission values', () => {
  const bad = { GITHUB_ACTIONS: 'false', GITHUB_JOB: 'other', GITHUB_REPOSITORY: 'foreign/fork', GITHUB_WORKFLOW: 'Other workflow', RUNNER_OS: 'Linux', RUNNER_TEMP: path.join(HOSTED_TEMP, 'other'), GITHUB_WORKSPACE: path.join(CHECKOUT, 'other'), GITHUB_RUN_ID: '0', GITHUB_RUN_ATTEMPT: '1.5' }
  for (const [key, value] of Object.entries(bad)) {
    assert.throws(() => requireHostedContext({ ...context(), [key]: value }, event(), 'darwin'), key)
  }
  for (const key of Object.keys(context())) {
    const env = context(); delete env[key]
    assert.throws(() => requireHostedContext(env, event(), 'darwin'), `missing ${key}`)
  }
  for (const value of ['0', '-1', ' 1', '1 ', '1x', '1.5', '9'.repeat(20)]) {
    assert.throws(() => requireHostedContext({ ...context(), GITHUB_RUN_ID: value }, event(), 'darwin'))
    assert.throws(() => requireHostedContext({ ...context(), GITHUB_RUN_ATTEMPT: value }, event(), 'darwin'))
  }
  assert.throws(() => requireHostedContext({ ...context(), TRUST_COMPONENT_SOURCE_SHA: 'not-a-sha' }, event(), 'darwin'))
})

test('every event identity guard rejects absent, foreign or mismatched PR identity', () => {
  for (const number of [undefined, null, 0, -1, 1.5, '42', Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => requireHostedContext(context(), { ...event(), number }, 'darwin'))
  }
  const foreignBase = event(); foreignBase.pull_request.base.repo.full_name = 'foreign/fork'
  const foreignEvent = event(); foreignEvent.repository.full_name = 'foreign/fork'
  const mismatchedHead = event(); mismatchedHead.pull_request.head.sha = 'c'.repeat(40)
  const missingHead = event(); delete missingHead.pull_request.head
  const mismatchedNumber = event(); mismatchedNumber.number = 43
  for (const value of [null, {}, foreignBase, foreignEvent, mismatchedHead, missingHead, mismatchedNumber]) {
    assert.throws(() => requireHostedContext(context(), value, 'darwin'))
  }
})

async function rejectsPreparation(result) {
  const state = { preparation: [] }, calls = []
  const run = async (...args) => { calls.push(args); return result }
  await assert.rejects(prepareStep('/synthetic-command', [], { PATH: 'synthetic' }, run, state))
  assert.equal(calls.length, 1)
  assert.equal(calls[0][3], 30000)
  assert.deepEqual(state.preparation, [safeStatus(result)])
}

test('preparation retains typed failure and rejects failed predecessors', async () => {
  await rejectsPreparation({ ...good(), ok: false, exitStatus: 7 })
})

test('preparation rejects an unclosed predecessor despite a success flag', async () => {
  await rejectsPreparation({ ...good(), reaped: false })
})

test('one add/remove/delete and independent before/after membership observations qualify only the component', async () => {
  const state = report(), mock = fakeLifecycle()
  await lifecycle(state, fixture, { PATH: '/usr/bin:/bin:/usr/sbin:/sbin' }, mock.run)
  assert.equal(componentPassed(state), true)
  assert.deepEqual(state.operations.map(item => item.stage), ['add', 'remove-admin-trust', 'delete-certificate'])
  assert.deepEqual(state.observations.map(item => item.stage), ['before-add', 'after-add', 'after-remove-before-delete', 'after-delete'])
  const commands = mock.calls.filter(item => item.command === '/usr/bin/sudo')
  assert.equal(commands.length, 3)
  assert(commands.every(item => item.timeoutMs === 30000 && item.args[0] === '/usr/bin/security'))
  assert.deepEqual(commands[1].args, ['/usr/bin/security', 'remove-trusted-cert', '-d', fixture.cert])
  assert.deepEqual(commands[2].args, ['/usr/bin/security', 'delete-certificate', '-Z', FINGERPRINT, '/Library/Keychains/System.keychain'])
  assert.equal(state.nativeAccepted, undefined)
})

test('partial add failure still attempts removal and deletion once; never qualifies', async () => {
  const state = report(), mock = fakeLifecycle({ addFails: true })
  await lifecycle(state, fixture, {}, mock.run)
  assert.equal(state.operations.length, 3)
  assert.equal(state.observations.length, 4)
  assert.equal(componentPassed(state), false)
})

test('timed-out removal remains fatal even if certificate deletion and absence reads succeed', async () => {
  const state = report(), mock = fakeLifecycle({ removeFails: true })
  await lifecycle(state, fixture, {}, mock.run)
  assert.deepEqual(state.operations[1], { stage: 'remove-admin-trust', ok: false, code: 'ETIMEDOUT', exitStatus: null, signal: 'SIGTERM', reaped: true })
  assert.equal(state.operations[2].ok, true)
  assert.equal(componentPassed(state), false)
})

test('unknown process ownership blocks subsequent trust mutation without leaking the exception', async () => {
  const state = report(), mock = fakeLifecycle({ throwingRemove: true })
  await lifecycle(state, fixture, {}, mock.run)
  assert.equal(state.operations[2].stage, 'delete-certificate')
  assert.equal(state.operations[2].code, 'predecessor-not-reaped')
  assert.equal(mock.calls.filter(item => item.command === '/usr/bin/sudo').length, 2)
  assert.equal(state.ownershipLost, true)
  assert.equal(componentPassed(state), false)
  assert(!JSON.stringify(state).includes('raw cleanup secret'))
})

test('failed intermediate read cannot skip either cleanup operation', async () => {
  const state = report(), mock = fakeLifecycle({ observerFailsAt: 1 })
  await lifecycle(state, fixture, {}, mock.run)
  assert.equal(state.operations.length, 3)
  assert.equal(componentPassed(state), false)
  assert(!JSON.stringify(state).includes('raw observer secret'))
})

test('failed post-removal observation still permits independent deletion after the reader exits', async () => {
  const state = report(), mock = fakeLifecycle({ observerFailsAt: 2 })
  await lifecycle(state, fixture, {}, mock.run)
  assert.equal(state.operations[2].ok, true)
  assert.equal(componentPassed(state), false)
})

test('certificate deletion alone cannot qualify retained admin trust', async () => {
  const state = report(), mock = fakeLifecycle({ retainedTrust: true })
  await lifecycle(state, fixture, {}, mock.run)
  assert.equal(state.operations[2].ok, true)
  assert.equal(componentPassed(state), false)
})

test('unavailable preflight never becomes zero entries and starts no mutation', async () => {
  const state = report(), mock = fakeLifecycle({ observerFailsAt: 0 })
  await assert.rejects(lifecycle(state, fixture, {}, mock.run))
  assert.equal(mock.calls.length, 1)
  assert.equal(state.operations.length, 0)
})

test('observer boundary copies only typed whitelist and exact fixture fingerprint', () => {
  const raw = { ...measured(false, 0), secret: 'never serialize this', rawTrust: ['private'], error: 'credentials', rights: { ...measured(false, 0).rights, unexpected: 'private' } }
  const output = observation(Buffer.from(JSON.stringify(raw)), FINGERPRINT)
  assert(!JSON.stringify(output).includes('private'))
  assert(!JSON.stringify(output).includes('credentials'))
  assert.throws(() => observation(Buffer.from(JSON.stringify(raw)), 'B'.repeat(64)))
  raw.targetStatus = -1
  assert.throws(() => observation(Buffer.from(JSON.stringify(raw)), FINGERPRINT))
  raw.targetPresent = null; raw.adminStatus = -1
  assert.throws(() => observation(Buffer.from(JSON.stringify(raw)), FINGERPRINT))
})

test('target no-store status stays unknown; only documented target absence is accepted', () => {
  const raw = measured(false, 0)
  raw.targetStatus = -25263; raw.targetPresent = null
  assert.equal(observation(Buffer.from(JSON.stringify(raw)), FINGERPRINT).targetPresent, null)
  raw.targetPresent = false
  assert.throws(() => observation(Buffer.from(JSON.stringify(raw)), FINGERPRINT))
})

test('typed command failures exclude message, argv, stdout, stderr and arbitrary signal/code', () => {
  const output = safeStatus({ ok: false, code: 'secret', exitStatus: 17, signal: 'secret', message: 'secret', output: 'secret', reaped: true })
  assert.deepEqual(output, { ok: false, code: 'other', exitStatus: 17, signal: null, reaped: true })
  assert(!JSON.stringify(output).includes('secret'))
})

function fakeChild(closeOnSignal = true) {
  const child = new EventEmitter()
  child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.signals = []
  child.unref = () => { child.unreferenced = true }
  child.kill = signal => { child.signals.push(signal); if (closeOnSignal) queueMicrotask(() => child.emit('close', null, signal)); return true }
  return child
}

test('bounded subprocess wrapper distinguishes timeout from ordinary nonzero exit and observes reaping', async () => {
  const timeoutChild = fakeChild()
  const timed = await invoke('/synthetic-command', [], {}, 2, () => timeoutChild)
  assert.deepEqual(safeStatus(timed), { ok: false, code: 'ETIMEDOUT', exitStatus: null, signal: 'SIGTERM', reaped: true })
  const failed = fakeChild()
  const waiting = invoke('/synthetic-command', [], {}, 100, () => failed)
  failed.emit('close', 23, null)
  assert.deepEqual(safeStatus(await waiting), { ok: false, code: null, exitStatus: 23, signal: null, reaped: true })
})

test('combined stdout/stderr bound kills owned process without retaining raw stderr', async () => {
  const child = fakeChild()
  const waiting = invoke('/synthetic-command', [], {}, 100, () => child)
  child.stderr.write(Buffer.alloc(16385, 's'))
  const result = await waiting
  assert.equal(result.code, 'output-limit')
  assert.equal(result.output.length, 0)
  assert.equal(result.reaped, true)
})

test('unreaped timeout is bounded, releases only Node handles and never SIGKILLs sudo', async () => {
  const child = fakeChild(false)
  const started = performance.now()
  const result = await invoke('/synthetic-command', [], {}, 2, () => child)
  assert.equal(result.reaped, false)
  assert.equal(result.ok, false)
  assert.equal(child.unreferenced, true)
  assert.equal(child.stdout.destroyed, true)
  assert.equal(child.stderr.destroyed, true)
  assert.deepEqual(child.signals, ['SIGTERM'])
  assert(performance.now() - started < 5000)
})

test('unreaped add blocks every subsequent command and does not claim cleanup', async () => {
  const state = report(), calls = []
  const run = async (command, args) => {
    calls.push({ command, args })
    if (command.endsWith('/observer')) return { ...good(), output: Buffer.from(JSON.stringify(measured(false, 0))) }
    return { ...good(), ok: false, code: 'ETIMEDOUT', exitStatus: null, signal: null, reaped: false }
  }
  await lifecycle(state, fixture, {}, run)
  assert.equal(calls.length, 2)
  assert.deepEqual(state.operations.slice(1).map(item => item.code), ['predecessor-not-reaped', 'predecessor-not-reaped'])
  assert.equal(componentPassed(state), false)
})

test('standalone workflow has no dispatch, secrets, write grants or release acceptance integration', () => {
  const workflow = fs.readFileSync(path.join(CHECKOUT, WORKFLOW_FILE), 'utf8')
  assert(workflow.includes('runs-on: macos-15'))
  assert(workflow.includes('timeout-minutes: 6'))
  assert(workflow.includes('ref: ${{ github.event.pull_request.head.sha }}'))
  assert(workflow.includes('github.event.pull_request.head.repo.full_name == github.repository'))
  assert(!/workflow_dispatch|workflow_call|pull_request_target|secrets:|: write|continue-on-error/.test(workflow))
  assert.equal((workflow.match(/run: node scripts\/hosted_trust_component\.cjs/g) || []).length, 1)
})
