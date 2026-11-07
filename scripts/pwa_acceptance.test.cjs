const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { SCENARIOS, loadCandidate, sha, binding } = require('./pwa_acceptance.cjs')

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
  assert.equal(loadCandidate(f.filename).assets.size, 3)
  assert.equal(binding(f.candidate).runAttempt, 2)
})
test('changed frontend is rejected', t => {
  const f = fixture(t); fs.writeFileSync(path.join(f.distribution, 'notifications-sw.js'), 'changed')
  assert.throws(() => loadCandidate(f.filename), /bytes changed/)
})
test('linked assets are rejected', t => {
  const f = fixture(t); const target = path.join(f.distribution, 'index.html')
  fs.unlinkSync(target); fs.symlinkSync(path.join(f.distribution, 'notifications-sw.js'), target)
  assert.throws(() => loadCandidate(f.filename), /Linked/)
})
test('path traversal and absolute inventory are rejected', t => {
  const f = fixture(t)
  for (const name of ['../outside', '/etc/passwd', 'a/../b', 'a\\b', 'a//b']) {
    f.candidate.frontendFiles = { [name]: 'a'.repeat(64) }; f.write()
    assert.throws(() => loadCandidate(f.filename), /frontend path/)
  }
})
test('release binding cannot be empty or invalid', t => {
  const f = fixture(t)
  f.candidate.candidateArtifactId = ''; f.write()
  assert.throws(() => loadCandidate(f.filename), /candidateArtifactId/)
})
test('missing actual worker fails', t => {
  const f = fixture(t); delete f.candidate.frontendFiles['notifications-sw.js']; f.write()
  assert.throws(() => loadCandidate(f.filename), /Missing/)
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
