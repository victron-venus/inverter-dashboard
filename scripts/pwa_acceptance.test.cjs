const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { SCENARIOS, loadCandidate, measuredFrontend, createPwaOutput, sha, binding, workspacePath, candidatePath, fixtureCertificate, waitFor } = require('./pwa_acceptance.cjs')

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pwa-contract-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const distribution = path.join(root, 'frontend'); fs.mkdirSync(distribution)
  const frontendFiles = {}
  for (const name of ['index.html', 'notifications-sw.js', 'manifest.webmanifest']) {
    fs.writeFileSync(path.join(distribution, name), name)
    frontendFiles[name] = sha(Buffer.from(name))
  }
  const candidate = { repository: 'victron-venus/test', sourceSha: 'a'.repeat(40), runId: 1, runAttempt: 2, candidateArtifactId: 3, candidateSha256: 'b'.repeat(64), distribution: 'frontend', frontendFiles }
  const filename = path.join(root, 'candidate.json')
  const write = () => fs.writeFileSync(filename, JSON.stringify(candidate))
  write()
  return { root, candidate, distribution, filename, write }
}

test('explicit lifecycle set distinguishes fixture subscription and real worker scope', () => {
  assert.equal(SCENARIOS.length, 9)
  assert.equal(new Set(SCENARIOS).size, 9)
  assert(SCENARIOS.includes('granted-permission-registers-ui-fixture-subscription'))
  assert(!SCENARIOS.some(name => name.includes('os-notification')))
})
test('exact files and producer binding load', t => {
  const f = fixture(t)
  assert.equal(loadCandidate(f.filename, f.root).assets.size, 3)
  assert.equal(binding(f.candidate).runAttempt, 2)
})
test('changed frontend is rejected', t => {
  const f = fixture(t); fs.writeFileSync(path.join(f.distribution, 'notifications-sw.js'), 'changed')
  assert.throws(() => loadCandidate(f.filename, f.root), /bytes changed/)
})
test('linked assets are rejected', t => {
  const f = fixture(t); const target = path.join(f.distribution, 'index.html')
  fs.unlinkSync(target); fs.symlinkSync(path.join(f.distribution, 'notifications-sw.js'), target)
  assert.throws(() => loadCandidate(f.filename, f.root), /Linked/)
})
test('path traversal and absolute inventory are rejected', t => {
  const f = fixture(t)
  for (const name of ['../outside', '/etc/passwd', 'a/../b', String.raw`a\b`, 'a//b']) {
    f.candidate.frontendFiles = { [name]: 'a'.repeat(64) }; f.write()
    assert.throws(() => loadCandidate(f.filename, f.root), /frontend path/)
  }
})
test('release binding cannot be empty or invalid', t => {
  const f = fixture(t)
  f.candidate.candidateArtifactId = ''; f.write()
  assert.throws(() => loadCandidate(f.filename, f.root), /candidateArtifactId/)
})
test('missing actual worker fails', t => {
  const f = fixture(t); delete f.candidate.frontendFiles['notifications-sw.js']; f.write()
  assert.throws(() => loadCandidate(f.filename, f.root), /Missing/)
})

test('native smoke emits bounded real WebSocket JSON frames', () => {
  const { websocketFrame } = require('./native_acceptance_smoke.cjs')
  const value = { battery_soc: 73 }
  const short = websocketFrame(value)
  assert.equal(short[0], 0x81)
  assert.equal(short[1], short.length - 2)
  assert.deepEqual(JSON.parse(short.subarray(2).toString()), value)
  const long = websocketFrame({ text: 'a'.repeat(200) })
  assert.equal(long[1], 126)
  assert.equal(long.readUInt16BE(2), long.length - 4)
  assert.throws(() => websocketFrame({ text: 'a'.repeat(65536) }))
})

test('native socket observer handles fragmented transport reads and ping frames', () => {
  const { clientFrameReader } = require('./native_acceptance_smoke.cjs')
  const seen = []
  const reader = clientFrameReader((opcode, payload) => seen.push({ opcode, payload: payload.toString() }))
  const mask = Buffer.from([1, 2, 3, 4])
  const body = Buffer.from('hello')
  const masked = Buffer.from(body.map((value, index) => value ^ mask[index % 4]))
  const frame = Buffer.concat([Buffer.from([0x81, 0x80 | body.length]), mask, masked])
  reader(frame.subarray(0, 1)); reader(frame.subarray(1, 5)); reader(frame.subarray(5))
  reader(Buffer.from([0x89, 0x80, 0, 0, 0, 0]))
  assert.deepEqual(seen, [{ opcode: 1, payload: 'hello' }, { opcode: 9, payload: '' }])
  assert.throws(() => reader(Buffer.from([0x81, 1, 0])), /Invalid client/)
})

test('permission override is bound to the isolated browser context and stays attached', async () => {
  const { setNotificationPermission } = require('./pwa_acceptance.cjs')
  const calls = []
  const session = { send: async (name, input) => { calls.push({ name, input }); return name === 'Target.getTargetInfo' ? { targetInfo: { browserContextId: 'fixture-context' } } : {} }, detach: () => { throw new Error('Detaching loses the override') } }
  await setNotificationPermission(session, 'https://127.0.0.1:123', 'denied')
  assert.equal(calls[1].name, 'Browser.setPermission')
  assert.equal(calls[1].input.browserContextId, 'fixture-context')
  assert.equal(calls[1].input.setting, 'denied')
})
test('default context permission override requires explicit disposable persistent profile', async () => {
  const { setNotificationPermission } = require('./pwa_acceptance.cjs')
  const calls = []
  const session = { send: async (name, input) => { calls.push({ name, input }); return { targetInfo: {} } } }
  await assert.rejects(setNotificationPermission(session, 'https://127.0.0.1:123', 'granted'), /isolated context/)
  await setNotificationPermission(session, 'https://127.0.0.1:123', 'granted', true)
  assert.equal(calls.at(-1).name, 'Browser.setPermission')
  assert.equal(calls.at(-1).input.setting, 'granted')
  assert.equal(calls.at(-1).input.browserContextId, undefined)
})


test('workspace boundary rejects outside paths, traversal, links and output symlinks', t => {
  const f = fixture(t)
  const outside = path.join(os.tmpdir(), 'outside-qualification')
  assert.throws(() => workspacePath(outside, f.root), /escapes/)
  assert.throws(() => workspacePath('../escape', f.root), /Invalid/)
  assert.throws(() => workspacePath(f.root, f.root), /escapes/)
  const linked = path.join(f.root, 'linked'); fs.symlinkSync(f.distribution, linked)
  assert.throws(() => workspacePath(path.join(linked, 'index.html'), f.root), /Linked/)
  const dangling = path.join(f.root, 'dangling'); fs.symlinkSync(outside, dangling)
  assert.throws(() => workspacePath(dangling, f.root), /Linked/)
  assert.equal(workspacePath('future/result.json', f.root), path.join(fs.realpathSync(f.root), 'future/result.json'))
})

test('candidate distribution cannot escape the trusted workspace', t => {
  const f = fixture(t)
  f.candidate.distribution = os.tmpdir(); f.write()
  assert.throws(() => loadCandidate(f.filename, f.root), /checkout-relative/)
})

test('fixture certificate uses fixed OpenSSL and supports its exact config on this host', t => {
  const f = fixture(t)
  const originalPath = process.env.PATH
  process.env.PATH = '/nonexistent-path'
  try {
    const generated = fixtureCertificate(f.root)
    assert(fs.readFileSync(generated.cert, 'utf8').includes('BEGIN CERTIFICATE'))
    const { certificateFingerprint } = require('./native_acceptance_smoke.cjs')
    assert.match(certificateFingerprint(generated.cert), /^[A-F0-9]{64}$/)
    assert(fs.readFileSync(generated.key, 'utf8').includes('PRIVATE KEY'))
  } finally { process.env.PATH = originalPath }
})

test('sequential bounded polling returns latest observation and rejects timeout', async () => {
  let calls = 0
  assert.equal(await waitFor(() => ++calls, value => value === 2, 'fixture', 3), 2)
  assert.equal(calls, 2)
  await assert.rejects(waitFor(() => false, Boolean, 'never ready', 1), /Timed out: never ready/)
})


test('WebSocket handshake matches the RFC6455 vector without claiming hash security', () => {
  const { websocketAccept } = require('./native_acceptance_smoke.cjs')
  assert.equal(websocketAccept('dGhlIHNhbXBsZSBub25jZQ=='), 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=')
  assert.throws(() => websocketAccept('not-a-client-nonce'), /Invalid/)
})


test('native WebDriver JavaScript bodies parse before any hosted browser is launched', () => {
  const { execFileSync } = require('node:child_process')
  const extract = `import ast, json, pathlib, sys
source = ast.parse(pathlib.Path(sys.argv[1]).read_text())
bodies = []
for node in ast.walk(source):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('script', 'until') and node.args and isinstance(node.args[0], ast.Constant):
        bodies.append(node.args[0].value)
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == 'script' and isinstance(value, ast.Constant) and isinstance(value.value, str):
                bodies.append(value.value)
print(json.dumps(bodies))`
  const bodies = JSON.parse(execFileSync('/usr/bin/python3', ['-c', extract, path.join(__dirname, 'native_acceptance_webdriver.py')], { encoding: 'utf8', timeout: 10000 }))
  assert.equal(bodies.length, 6)
  for (const body of bodies) {
    // --check parses stdin only; it does not run browser code or any UI.
    execFileSync(process.execPath, ['--check'], { input: `function webdriverScript() { ${body} }`, timeout: 10000 })
  }
})


test('candidate metadata accepts only canonical relative paths under the checkout', t => {
  const f = fixture(t)
  assert.equal(candidatePath('frontend/index.html', f.root), path.join(fs.realpathSync(f.root), 'frontend/index.html'))
  for (const invalid of ['../escape', '/absolute', './frontend', 'frontend//index.html', 'frontend/../index.html', String.raw`frontend\index.html`, 'C:/host/path', 'frontend/']) {
    assert.throws(() => candidatePath(invalid, f.root), /checkout-relative/)
  }
})


function pythonRuntimeFixture(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'native-runtime-contract-')))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const roots = { toolcache: path.join(root, 'toolcache/Python'), framework: path.join(root, 'Frameworks/Python.framework/Versions') }
  const install = path.join(roots.toolcache, '3.12.13', 'arm64')
  const framework = path.join(roots.framework, '3.12')
  fs.mkdirSync(install, { recursive: true }); fs.mkdirSync(path.join(framework, 'bin'), { recursive: true })
  const executable = path.join(framework, 'bin/python3.12')
  fs.writeFileSync(executable, 'Synthetic nonexecuted interpreter path fixture'); fs.chmodSync(executable, 0o700)
  fs.symlinkSync(path.join(framework, 'bin'), path.join(install, 'bin'))
  fs.symlinkSync('python3.12', path.join(framework, 'bin/python'))
  return { root, roots, install, executable, supplied: path.join(install, 'bin/python') }
}

test('setup-python selection resolves only documented toolcache-to-framework links', t => {
  const { resolveSetupPython } = require('./native_acceptance_runtime.cjs')
  const f = pythonRuntimeFixture(t)
  assert.equal(resolveSetupPython(f.supplied, '3.12.13', 'arm64', f.roots), f.executable)
  for (const supplied of ['/usr/bin/python3', 'python3', path.join(f.root, 'injected/python'), f.supplied.replace('/bin/', '/bin/../bin/')]) {
    assert.throws(() => resolveSetupPython(supplied, '3.12.13', 'arm64', f.roots), /exact setup-python/)
  }
  assert.throws(() => resolveSetupPython(f.supplied, '3.9.6', 'arm64', f.roots), /3.12 version/)
  assert.throws(() => resolveSetupPython(f.supplied, '3.12.12', 'arm64', f.roots), /exact setup-python/)
})

test('setup-python selection rejects redirected runtime links and nonexecutables', t => {
  const { resolveSetupPython } = require('./native_acceptance_runtime.cjs')
  const f = pythonRuntimeFixture(t)
  fs.chmodSync(f.executable, 0o600)
  assert.throws(() => resolveSetupPython(f.supplied, '3.12.13', 'arm64', f.roots), /regular executable/)
  fs.chmodSync(f.executable, 0o700)
  const outside = path.join(f.root, 'outside'); fs.mkdirSync(outside)
  fs.writeFileSync(path.join(outside, 'python'), 'not executed')
  fs.unlinkSync(path.join(f.install, 'bin')); fs.symlinkSync(outside, path.join(f.install, 'bin'))
  assert.throws(() => resolveSetupPython(f.supplied, '3.12.13', 'arm64', f.roots), /escaped/)
})

test('measured interpreter identity must match the pinned action output', () => {
  const { validatePythonMeasurement } = require('./native_acceptance_runtime.cjs')
  const executable = '/approved/python3.12'
  const measured = { implementation: 'cpython', version: [3, 12, 13], executable }
  validatePythonMeasurement(measured, executable, '3.12.13')
  assert.throws(() => validatePythonMeasurement({ ...measured, version: [3, 11, 0] }, executable, '3.12.13'), /version differs/)
  assert.throws(() => validatePythonMeasurement({ ...measured, executable: '/unapproved/python' }, executable, '3.12.13'), /executable differs/)
})


test('candidate CSP confines resource classes to the fixture without a host firewall claim', () => {
  const { fixturePolicy } = require('./native_acceptance_network.cjs')
  const policy = fixturePolicy('https://127.0.0.1:12345')
  for (const required of ["default-src 'none'", "script-src 'self'", "connect-src 'self' wss://127.0.0.1:12345", "form-action 'none'", "frame-src 'none'", 'report-uri /__native/csp-report']) assert(policy.includes(required))
  assert.throws(() => fixturePolicy('https://example.com'), /loopback/)
})

test('native finalizer rejects pending cleanup, commands and CSP violations before passing', () => {
  const { finalizeNativeReceipt } = require('./native_acceptance_network.cjs')
  const provisional = { scope: 'native-smoke', status: 'pending-parent-validation', sessionCleanupSucceeded: true, cspProbeObserved: true }
  const network = { probeReports: 1, candidateViolations: 0, malformedReports: 0, httpWrites: 0, policySha256: 'a'.repeat(64) }
  const counters = { applicationFrames: 0, protocolErrors: 0 }
  const result = finalizeNativeReceipt(provisional, network, counters, [])
  assert.equal(result.status, 'passed'); assert.equal(result.parentValidation.complete, true)
  assert.equal(result.networkIsolation.scope, 'candidate-document-resources')
  for (const changed of [{ ...provisional, status: 'passed' }, { ...provisional, sessionCleanupSucceeded: false }, { ...provisional, sessionCleanupFailed: true }, { ...provisional, cspProbeObserved: false }]) {
    assert.throws(() => finalizeNativeReceipt(changed, network, counters, []))
  }
  for (const key of ['candidateViolations', 'malformedReports', 'httpWrites']) {
    assert.throws(() => finalizeNativeReceipt(provisional, { ...network, [key]: 1 }, counters, []))
  }
  for (const probeReports of [0, 2]) assert.throws(() => finalizeNativeReceipt(provisional, { ...network, probeReports }, counters, []))
  assert.throws(() => finalizeNativeReceipt(provisional, network, { ...counters, applicationFrames: 1 }, []))
  assert.throws(() => finalizeNativeReceipt(provisional, network, { ...counters, protocolErrors: 1 }, []))
  assert.throws(() => finalizeNativeReceipt(provisional, network, counters, ['Trust cleanup failed']))
})

test('fixture HTTP guard counts settings writes and actual CSP reports', async t => {
  const http = require('node:http')
  const { createNetworkGuard } = require('./native_acceptance_network.cjs')
  const guard = createNetworkGuard()
  const server = http.createServer(guard.wrap(() => 'https://127.0.0.1:12345', (_req, res) => res.end('fixture')))
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => { server.closeAllConnections(); server.close() })
  const origin = `http://127.0.0.1:${server.address().port}`
  const page = await fetch(origin)
  assert(page.headers.get('content-security-policy').includes("default-src 'none'")); await page.text()
  const write = await fetch(origin + '/api/settings', { method: 'POST', body: '{}' }); await write.text()
  assert.equal(write.status, 405); assert.equal(guard.observed.httpWrites, 1)
  const post = async blocked => {
    const response = await fetch(origin + '/__native/csp-report', { method: 'POST', body: JSON.stringify({ 'csp-report': { 'blocked-uri': blocked, 'effective-directive': 'connect-src', disposition: 'enforce', 'document-uri': 'https://127.0.0.1:12345/', 'original-policy': page.headers.get('content-security-policy') } }) })
    assert.equal(response.status, 204)
  }
  await post('https://127.0.0.1:65534/__native-csp-probe__')
  await post('https://example.invalid/private-path-not-retained')
  const status = await (await fetch(origin + '/__native/csp-status')).json()
  assert.equal(status.probeReports, 1); assert.equal(status.candidateViolations, 1)
  assert.match(status.policySha256, /^[a-f0-9]{64}$/)
  assert(!JSON.stringify(status).includes('private-path'))
})


test('fixture server teardown requires close acknowledgement after forced cleanup', async () => {
  const { closeFixtureServer } = require('./native_acceptance_smoke.cjs')
  let callback, forced = false
  await closeFixtureServer({ close(done) { callback = done }, closeAllConnections() { forced = true; callback() } }, 1, 100)
  assert.equal(forced, true)
  await assert.rejects(closeFixtureServer({ close() { /* Synthetic hung listener: no completion acknowledgement. */ }, closeAllConnections() { /* Force-close also hangs in this fixture. */ } }, 1, 10), /did not close/)
  await assert.rejects(closeFixtureServer({ close() { throw new Error('synchronous close failure') }, closeAllConnections() { throw new Error('unexpected force') } }, 1, 10), /synchronous close failure/)
  await assert.rejects(closeFixtureServer({ close() { /* Synthetic listener awaits force-close. */ }, closeAllConnections() { throw new Error('force-close failure') } }, 1, 10), /force-close failure/)
  await assert.rejects(closeFixtureServer({ close(done) { done(new Error('listener failure')) }, closeAllConnections() { throw new Error('unexpected force') } }, 1, 10), /listener failure/)
})


test('WebKit legacy report normalization stays bound to the enforced policy and origin', () => {
  const { createNetworkGuard, fixturePolicy, PROBE_ORIGIN } = require('./native_acceptance_network.cjs')
  const guard = createNetworkGuard(), origin = 'https://127.0.0.1:12345'
  guard.wrap(() => origin, () => {} )({ url: '/', method: 'GET' }, { setHeader() { /* Header value is independently reconstructed below. */ } })
  const report = { 'blocked-uri': PROBE_ORIGIN + '/__native-csp-probe__', 'effective-directive': 'connect-src', 'document-uri': origin + '/', 'original-policy': fixturePolicy(origin) }
  guard.acceptReport({ 'csp-report': report })
  assert.equal(guard.observed.probeReports, 1)
  guard.acceptReport({ 'csp-report': { ...report, disposition: 'report' } })
  assert.equal(guard.observed.probeReports, 1); assert.equal(guard.observed.candidateViolations, 1)
  for (const changed of [{ ...report, 'original-policy': "default-src *" }, { ...report, 'document-uri': 'https://example.invalid/' }, { ...report, 'original-policy': undefined }]) {
    assert.throws(() => guard.acceptReport({ 'csp-report': changed }), /policy and origin/)
  }
})


test('admin trust and certificate removal are bounded and attempted independently', () => {
  const { removeFixtureTrust } = require('./native_acceptance_smoke.cjs')
  const calls = []
  const invoke = (program, args, options) => { calls.push({ program, args, options }) }
  assert.deepEqual(removeFixtureTrust('/fixture/cert.pem', 'A'.repeat(64), invoke), [])
  assert.deepEqual(calls.map(call => call.args), [
    ['/usr/bin/security', 'remove-trusted-cert', '-d', '/fixture/cert.pem'],
    ['/usr/bin/security', 'delete-certificate', '-Z', 'A'.repeat(64), '/Library/Keychains/System.keychain'],
  ])
  for (const call of calls) { assert.equal(call.program, '/usr/bin/sudo'); assert.equal(call.options.timeout, 30000) }
  const attempted = []
  const fail = (_program, args) => { attempted.push(args[1]); throw new Error('synthetic cleanup failure') }
  assert.equal(removeFixtureTrust('/fixture/cert.pem', 'A'.repeat(64), fail).length, 2)
  assert.deepEqual(attempted, ['remove-trusted-cert', 'delete-certificate'])
})


test('PWA output refuses existing candidate and results without deleting either', t => {
  const f = fixture(t), output = path.join(f.root, 'results')
  fs.mkdirSync(output)
  const candidate = path.join(output, 'result.json'), before = fs.readFileSync(f.filename)
  fs.writeFileSync(candidate, before)
  assert.throws(() => createPwaOutput(output, f.root), /must be fresh/)
  assert.deepEqual(fs.readFileSync(candidate), before)
  const fresh = path.join(f.root, 'new-output')
  assert.equal(createPwaOutput(fresh, f.root), path.join(fs.realpathSync(f.root), 'new-output'))
  assert.throws(() => createPwaOutput(fresh, f.root), /must be fresh/)
})

test('measured inventory binds every actual served buffer, independently of metadata', t => {
  const f = fixture(t), loaded = loadCandidate(f.filename, f.root)
  assert.deepEqual(measuredFrontend(loaded.assets, loaded.candidate), f.candidate.frontendFiles)
  const changed = new Map(loaded.assets)
  changed.set('/index.html', Buffer.from('different served page'))
  assert.throws(() => measuredFrontend(changed, loaded.candidate), /inventory differs/)
  const relabelled = { ...loaded.candidate, frontendFiles: { ...loaded.candidate.frontendFiles, 'index.html': 'f'.repeat(64) } }
  assert.throws(() => measuredFrontend(loaded.assets, relabelled), /inventory differs/)
  const omitted = new Map(loaded.assets); omitted.delete('/index.html')
  assert.throws(() => measuredFrontend(omitted, loaded.candidate), /inventory differs/)
})

test('Safari startup uses a ten-second elapsed deadline and rejects exited drivers', async () => {
  const { waitForNativeDriver } = require('./native_acceptance_smoke.cjs')
  const driver = { exitCode: null, signalCode: null }
  let elapsed = 0, reads = 0
  const controls = { now: () => elapsed, pause: async milliseconds => { elapsed += milliseconds }, read: async () => { reads++; return { ok: elapsed >= 9000 } } }
  await waitForNativeDriver(driver, 'http://127.0.0.1:12345/status', controls)
  assert.equal(elapsed, 9000); assert(reads > 40)
  elapsed = 0
  await assert.rejects(waitForNativeDriver(driver, 'http://127.0.0.1:12345/status', { ...controls, read: async () => ({ ok: false }) }), /Timed out/)
  assert.equal(elapsed, 10000)
  elapsed = 0
  await assert.rejects(waitForNativeDriver(driver, 'http://127.0.0.1:12345/status', { ...controls, read: async () => { elapsed = 10001; return { ok: true } } }), /Timed out/)
  await assert.rejects(waitForNativeDriver({ exitCode: 1, signalCode: null }, 'http://127.0.0.1:12345/status', controls), /exited/)
})
