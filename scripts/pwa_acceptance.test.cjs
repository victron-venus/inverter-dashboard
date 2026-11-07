const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { SCENARIOS, loadCandidate, sha, binding, workspacePath, fixtureCertificate, waitFor } = require('./pwa_acceptance.cjs')

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pwa-contract-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const distribution = path.join(root, 'frontend'); fs.mkdirSync(distribution)
  const frontendFiles = {}
  for (const name of ['index.html', 'notifications-sw.js', 'manifest.webmanifest']) {
    fs.writeFileSync(path.join(distribution, name), name)
    frontendFiles[name] = sha(Buffer.from(name))
  }
  const candidate = { repository: 'victron-venus/test', sourceSha: 'a'.repeat(40), runId: 1, runAttempt: 2, candidateArtifactId: 3, candidateSha256: 'b'.repeat(64), distribution, frontendFiles }
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
  assert.throws(() => loadCandidate(f.filename, f.root), /escapes/)
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
  assert.equal(bodies.length, 5)
  for (const body of bodies) {
    // --check parses stdin only; it does not run browser code or any UI.
    execFileSync(process.execPath, ['--check'], { input: `function webdriverScript() { ${body} }`, timeout: 10000 })
  }
})
