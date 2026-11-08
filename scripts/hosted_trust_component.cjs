#!/usr/bin/env node
/** One hosted-only admin-trust experiment. This is never native/release acceptance. */
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawn } = require('node:child_process')

const CHECKOUT = path.resolve(__dirname, '..')
const REPOSITORY = 'victron-venus/inverter-dashboard'
const WORKFLOW = 'Hosted macOS trust component'
const WORKFLOW_FILE = '.github/workflows/hosted-trust-component.yml'
const HOSTED_TEMP = '/Users/runner/work/_temp'
const SYSTEM_KEYCHAIN = '/Library/Keychains/System.keychain'
const SYSTEM_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'
const OUTPUT = '.hosted-trust-component'
const MAX_OUTPUT = 16384
const NO_ITEM = -25300
const NO_SETTINGS = -25263
const SHA = /^[a-f0-9]{40}$/
const RIGHT_NAMES = ['com.apple.trust-settings.admin', 'config.modify.trust-settings']
const CONTEXT_KEYS = ['CI', 'GITHUB_ACTIONS', 'GITHUB_EVENT_NAME', 'GITHUB_JOB', 'GITHUB_REPOSITORY', 'GITHUB_WORKFLOW', 'GITHUB_WORKFLOW_REF', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'RUNNER_OS', 'RUNNER_ENVIRONMENT', 'TRUST_COMPONENT_MODE', 'TRUST_COMPONENT_SOURCE_SHA']

function requireHostedContext(env, event, platform = process.platform) {
  assert.equal(platform, 'darwin', 'Component requires hosted macOS')
  const exact = { CI: 'true', GITHUB_ACTIONS: 'true', GITHUB_EVENT_NAME: 'pull_request', GITHUB_JOB: 'trust-lifecycle', GITHUB_REPOSITORY: REPOSITORY, GITHUB_WORKFLOW: WORKFLOW, RUNNER_OS: 'macOS', RUNNER_ENVIRONMENT: 'github-hosted', TRUST_COMPONENT_MODE: 'admin-trust-lifecycle-v1', RUNNER_TEMP: HOSTED_TEMP, GITHUB_WORKSPACE: CHECKOUT }
  for (const [key, value] of Object.entries(exact)) assert.equal(env[key], value, 'Hosted component context mismatch')
  assert.match(env.TRUST_COMPONENT_SOURCE_SHA || '', SHA, 'Source SHA required')
  assert.match(env.GITHUB_RUN_ID || '', /^[1-9]\d{0,18}$/)
  assert.match(env.GITHUB_RUN_ATTEMPT || '', /^[1-9]\d{0,3}$/)
  assert(Number.isSafeInteger(event?.number) && event.number > 0, 'PR event required')
  assert.equal(env.GITHUB_WORKFLOW_REF, `${REPOSITORY}/${WORKFLOW_FILE}@refs/pull/${event.number}/merge`)
  assert.equal(event.pull_request?.head?.repo?.full_name, REPOSITORY, 'Fork execution is forbidden')
  assert.equal(event.pull_request?.base?.repo?.full_name, REPOSITORY)
  assert.equal(event.repository?.full_name, REPOSITORY)
  assert.equal(event.pull_request?.head?.sha, env.TRUST_COMPONENT_SOURCE_SHA)
  return { sourceSha: env.TRUST_COMPONENT_SOURCE_SHA, runId: env.GITHUB_RUN_ID, runAttempt: env.GITHUB_RUN_ATTEMPT, pullRequest: event.number }
}

function regularPath(filename, directory = false) {
  const absolute = path.resolve(filename)
  assert.equal(absolute, filename, 'Canonical path required')
  let cursor = path.parse(absolute).root
  for (const part of absolute.slice(cursor.length).split(path.sep)) {
    cursor = path.join(cursor, part)
    const entry = fs.lstatSync(cursor)
    assert(!entry.isSymbolicLink(), 'Linked component path forbidden')
    if (cursor !== absolute) assert(entry.isDirectory(), 'Directory ancestor required')
  }
  const entry = fs.lstatSync(absolute)
  assert(directory ? entry.isDirectory() : entry.isFile() && entry.nlink === 1, 'Regular owned path required')
  return entry
}

function boundedRead(filename, limit) {
  const entry = regularPath(filename)
  assert(entry.size > 0 && entry.size <= limit, 'Input size bound')
  const value = fs.readFileSync(filename)
  assert(value.length <= limit, 'Input changed beyond size bound')
  return value
}

function executionContext(env = process.env) {
  // Reject local invocation before opening even the event file or starting a process.
  assert.equal(process.platform, 'darwin', 'Hosted macOS only')
  assert.equal(env.GITHUB_ACTIONS, 'true', 'Hosted Actions only')
  assert.equal(env.RUNNER_ENVIRONMENT, 'github-hosted', 'Disposable hosted runner only')
  assert.equal(env.TRUST_COMPONENT_MODE, 'admin-trust-lifecycle-v1', 'Explicit component mode required')
  const eventPath = path.join(HOSTED_TEMP, '_github_workflow/event.json')
  assert.equal(env.GITHUB_EVENT_PATH, eventPath, 'Fixed native event path required')
  const event = JSON.parse(boundedRead(eventPath, 1048576))
  const identity = requireHostedContext(env, event)
  regularPath(CHECKOUT, true)
  regularPath(HOSTED_TEMP, true)
  assert(process.getuid() > 0 && process.geteuid() === process.getuid(), 'Unprivileged runner owner required')
  const childEnv = { PATH: SYSTEM_PATH, LANG: 'C', LC_ALL: 'C' }
  for (const key of CONTEXT_KEYS) childEnv[key] = env[key]
  childEnv.TRUST_COMPONENT_PR_NUMBER = String(identity.pullRequest)
  return { identity, childEnv }
}

function exitStatus(code, signal) {
  return { exitStatus: Number.isInteger(code) && code >= 0 && code <= 255 ? code : null, signal: typeof signal === 'string' && Object.hasOwn(os.constants.signals, signal) ? signal : null }
}

function invoke(command, args, env, timeoutMs, launch = spawn) {
  // No shell and no inherited secrets; fixed callers below supply every command.
  return new Promise(resolve => {
    const state = { ok: false, code: null, exitStatus: null, signal: null, reaped: false, output: Buffer.alloc(0) }
    let child, timer, deadline, finished = false, terminating = false, size = 0
    const finish = () => {
      if (finished) return
      finished = true
      clearTimeout(timer); clearTimeout(deadline)
      if (child && !state.reaped) {
        // Do not SIGKILL sudo alone: its privileged child could be orphaned.
        // Stop local pipe retention and detach the Node handle, not the OS process.
        // The caller retains the fixture, blocks further mutations and fails.
        child.stdout.destroy(); child.stderr.destroy(); child.unref()
      }
      resolve(state)
    }
    const kill = signal => { try { child.kill(signal) } catch { state.code ||= 'signal-error' } }
    const terminate = code => {
      if (terminating || finished) return
      terminating = true
      state.code ||= code
      kill('SIGTERM')
      deadline = setTimeout(finish, 4000)
    }
    try { child = launch(command, args, { env, stdio: ['ignore', 'pipe', 'pipe'], shell: false }) }
    catch { state.code = 'spawn-error'; finish(); return }
    const consume = (bytes, keep) => {
      size += bytes.length
      if (size > MAX_OUTPUT) { terminate('output-limit'); return }
      if (keep) state.output = Buffer.concat([state.output, bytes])
    }
    child.stdout.on('data', bytes => consume(bytes, true))
    child.stderr.on('data', bytes => consume(bytes, false))
    child.once('error', () => { state.code ||= 'spawn-error' })
    child.once('close', (code, signal) => {
      Object.assign(state, exitStatus(code, signal), { reaped: true })
      state.ok = state.code === null && code === 0
      finish()
    })
    timer = setTimeout(() => terminate('ETIMEDOUT'), timeoutMs)
  })
}

function safeStatus(result) {
  const allowed = [null, 'ETIMEDOUT', 'output-limit', 'spawn-error', 'signal-error', 'predecessor-not-reaped']
  return { ok: result.ok === true, code: allowed.includes(result.code) ? result.code : 'other', ...exitStatus(result.exitStatus, result.signal), reaped: result.reaped === true }
}

function integer(value, min = -2147483648, max = 2147483647) {
  assert(Number.isInteger(value) && value >= min && value <= max, 'Invalid observer number')
  return value
}

function nullableBoolean(value) {
  assert(value === null || typeof value === 'boolean', 'Invalid observer boolean')
  return value
}

function rightSummary(value) {
  assert(['allow', 'deny', 'user', 'rule', 'other', 'unavailable'].includes(value?.class), 'Invalid right class')
  return { osStatus: integer(value.osStatus), class: value.class, allowRoot: nullableBoolean(value.allowRoot), ruleCount: value.ruleCount === null ? null : integer(value.ruleCount, 0, 1024), hasIsRootRule: nullableBoolean(value.hasIsRootRule) }
}

function observation(buffer, fingerprint) {
  assert(buffer.length <= MAX_OUTPUT, 'Observer size bound')
  const data = JSON.parse(buffer.toString('utf8'))
  assert.equal(data.fingerprint256, fingerprint, 'Observer certificate binding mismatch')
  const adminStatus = integer(data.adminStatus)
  const targetStatus = integer(data.targetStatus)
  const adminCount = data.adminCount === null ? null : integer(data.adminCount, 0, 100000)
  const targetPresent = nullableBoolean(data.targetPresent)
  if (adminStatus === NO_SETTINGS) assert.equal(adminCount, 0, 'Absent admin store must have zero records')
  else if (adminStatus !== 0) assert.equal(adminCount, null, 'Unproven admin count')
  let expectedTarget = null
  if (targetStatus === 0) expectedTarget = true
  if (targetStatus === NO_ITEM) expectedTarget = false
  assert.equal(targetPresent, expectedTarget, 'Unproven target membership')
  const rights = {}
  for (const name of RIGHT_NAMES) rights[name] = rightSummary(data.rights?.[name])
  return { fingerprint256: fingerprint, uid: integer(data.uid, 0), effectiveUid: integer(data.effectiveUid, 0), adminStatus, adminCount, targetStatus, targetPresent, sipUnrestrictedFsCheck: data.sipUnrestrictedFsCheck === null ? null : integer(data.sipUnrestrictedFsCheck), rights }
}

async function containedRun(report, run, command, args, env, timeoutMs) {
  const failed = { ok: false, code: 'predecessor-not-reaped', exitStatus: null, signal: null, reaped: false, output: Buffer.alloc(0) }
  if (report.ownershipLost) return failed
  let result
  try { result = await run(command, args, env, timeoutMs) }
  catch { result = { ...failed, code: 'spawn-error' } }
  if (!result.reaped) report.ownershipLost = true
  return result
}

async function observe(stage, report, run, fixture, env) {
  const result = await containedRun(report, run, path.join(fixture.directory, 'observer'), [], env, 10000)
  const record = { stage, command: safeStatus(result), measured: null }
  if (result.ok) {
    try { record.measured = observation(result.output, fixture.fingerprint) }
    catch { record.invalidOutput = true }
  }
  report.observations.push(record)
  return record.measured
}

async function action(stage, args, report, run, env) {
  const result = await containedRun(report, run, '/usr/bin/sudo', ['/usr/bin/security', ...args], env, 30000)
  report.operations.push({ stage, ...safeStatus(result) })
  return result.ok
}

async function lifecycle(report, fixture, env, run = invoke) {
  const before = await observe('before-add', report, run, fixture, env)
  assert(before?.targetPresent === false && before.adminCount !== null, 'Initial target absence must be measured')
  // A reaped failed add still requires both independent cleanup attempts. An
  // unconfirmed live predecessor blocks later processes instead of overlapping it.
  try {
    const added = await action('add', ['add-trusted-cert', '-d', '-r', 'trustRoot', '-k', SYSTEM_KEYCHAIN, fixture.cert], report, run, env)
    const afterAdd = await observe('after-add', report, run, fixture, env)
    report.addVerified = added && afterAdd?.targetPresent === true && afterAdd.adminCount === before.adminCount + 1
  } finally {
    await action('remove-admin-trust', ['remove-trusted-cert', '-d', fixture.cert], report, run, env)
    await observe('after-remove-before-delete', report, run, fixture, env)
    await action('delete-certificate', ['delete-certificate', '-Z', fixture.fingerprint, SYSTEM_KEYCHAIN], report, run, env)
    await observe('after-delete', report, run, fixture, env)
  }
}

function componentPassed(report) {
  if (report.ownershipLost || report.addVerified !== true || report.operations.length !== 3 || report.observations.length !== 4) return false
  if (report.operations.some(item => !item.ok || !item.reaped)) return false
  if (report.observations.some(item => !item.command.ok || !item.command.reaped || !item.measured)) return false
  const [before, , removed, deleted] = report.observations.map(item => item.measured)
  return removed.targetPresent === false && deleted.targetPresent === false && removed.adminCount === before.adminCount && deleted.adminCount === before.adminCount
}

async function prepareStep(command, args, env, run, report) {
  const result = await run(command, args, env, 30000)
  report.preparation.push(safeStatus(result))
  assert(result.ok && result.reaped, 'Component preparation failed')
}

async function prepare(fixture, env, run, report) {
  const config = '[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=v3\n[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n'
  fs.writeFileSync(path.join(fixture.directory, 'openssl.cnf'), config, { flag: 'wx', mode: 0o600 })
  // Each step must succeed and close before the next one starts. In particular,
  // DER conversion consumes the certificate produced by the preceding step.
  await prepareStep('/usr/bin/clang', ['-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-framework', 'Foundation', '-framework', 'Security', path.join(__dirname, 'hosted_trust_observer.m'), '-o', path.join(fixture.directory, 'observer')], env, run, report)
  await prepareStep('/usr/bin/openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-config', path.join(fixture.directory, 'openssl.cnf'), '-keyout', path.join(fixture.directory, 'key.pem'), '-out', fixture.cert], env, run, report)
  await prepareStep('/usr/bin/openssl', ['x509', '-in', fixture.cert, '-outform', 'DER', '-out', path.join(fixture.directory, 'fixture.der')], env, run, report)
  const cert = new crypto.X509Certificate(boundedRead(fixture.cert, 65536))
  fixture.fingerprint = cert.fingerprint256.replaceAll(':', '')
  assert.match(fixture.fingerprint, /^[A-F0-9]{64}$/)
}

async function execute() {
  const { identity, childEnv } = executionContext()
  const head = await invoke('/usr/bin/git', ['-C', CHECKOUT, 'rev-parse', 'HEAD'], childEnv, 10000)
  assert(head.ok && head.output.toString('utf8').trim() === identity.sourceSha, 'Checked-out source must match PR head')
  const output = path.join(CHECKOUT, OUTPUT)
  fs.mkdirSync(output, { mode: 0o700 })
  const report = { schema: 'hosted-trust-component-v1', ...identity, componentPassed: false, nativeAccepted: false, releaseAccepted: false, internalWaitPhase: 'unknown', phaseClassifier: 'not-performed', operations: [], observations: [], preparation: [] }
  const run = (command, args, env, timeoutMs) => containedRun(report, invoke, command, args, env, timeoutMs)
  let directory
  try {
    directory = fs.mkdtempSync(path.join(HOSTED_TEMP, 'native-trust-'))
    fs.chmodSync(directory, 0o700)
    const fixture = { directory, cert: path.join(directory, 'cert.pem') }
    const env = { ...childEnv, TRUST_COMPONENT_DIRECTORY: directory }
    await prepare(fixture, env, run, report)
    const uid = await run('/usr/bin/sudo', ['/usr/bin/id', '-u'], env, 10000)
    assert(uid.ok && uid.output.toString('utf8').trim() === '0', 'Privileged operation identity unverified')
    report.privilegedEffectiveUid = 0
    await lifecycle(report, fixture, env, run)
    report.componentPassed = componentPassed(report)
  } catch { report.componentError = 'component-failed' }
  finally {
    if (directory && report.ownershipLost) {
      report.componentPassed = false
      report.fixtureRetainedUntilHostedRunnerDisposal = true
    } else if (directory) {
      try { fs.rmSync(directory, { recursive: true, force: false }) }
      catch { report.componentPassed = false; report.temporaryCleanupFailed = true }
    }
  }
  // Both success and failure retain only the whitelist; no stdout/stderr/arguments/error text.
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
  assert(report.componentPassed, 'Hosted trust component failed; inspect sanitized result.json')
}

if (require.main === module) execute().catch(() => { process.stderr.write('Hosted trust component failed\n'); process.exitCode = 1 })
module.exports = { requireHostedContext, executionContext, invoke, safeStatus, observation, lifecycle, componentPassed, prepareStep, CHECKOUT, HOSTED_TEMP, REPOSITORY, WORKFLOW, WORKFLOW_FILE }
