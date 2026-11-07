const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { contract, verifyScenarioContract, verifyCandidateInventory } = require('./browser-scenarios-contract.cjs')
const { candidatePath } = require('./pwa_acceptance.cjs')
const valid = () => ({ testsPassed: contract.scenarios.length, testsFailed: 0, scenarios: [...contract.scenarios] })
test('accepts exactly the versioned application inventory, independent of execution order', () => {
  const evidence = valid(); evidence.scenarios.reverse()
  assert.equal(verifyScenarioContract(evidence).required, contract.scenarios.length)
})
for (const [name, mutate] of [
  ['missing behavior despite positive count', value => value.scenarios.pop()],
  ['substituted behavior', value => { value.scenarios[0] = 'synthetic-pass' }],
  ['duplicate behavior', value => { value.scenarios[0] = value.scenarios[1] }],
  ['reported failure', value => { value.testsFailed = 1 }],
  ['inflated count', value => { value.testsPassed++ }],
  ['empty evidence', value => { value.scenarios = [] }],
]) test(`rejects ${name}`, () => { const evidence = valid(); mutate(evidence); assert.throws(() => verifyScenarioContract(evidence)) })


const { assertAgeWithinObservation, assertNoCommands } = require('./browser-scenarios.cjs')
test('source age uses measured wall-clock bounds across a minute boundary', () => {
  const occurredAt = '2026-11-01T08:30:00Z'
  const instant = Date.parse(occurredAt)
  const before = instant + 75 * 60000 + 59999
  const after = instant + 76 * 60000 + 1
  assertAgeWithinObservation('1h 15m ago', occurredAt, before, after)
  assertAgeWithinObservation('1h 16m ago', occurredAt, before, after)
  assert.throws(() => assertAgeWithinObservation('1h 17m ago', occurredAt, before, after))
  assert.throws(() => assertAgeWithinObservation('just now', occurredAt, before, after))
})
test('source age does not retain a fixed minute label after elapsed test waits', () => {
  const occurredAt = '2026-11-01T08:30:00Z'
  const now = Date.parse(occurredAt) + 78 * 60000 + 10
  assertAgeWithinObservation('1h 18m ago', occurredAt, now, now + 10)
  assert.throws(() => assertAgeWithinObservation('1h 15m ago', occurredAt, now, now + 10))
})
test('negative command observation catches an asynchronously delivered command', async () => {
  const fixture = { commands: [] }
  const delivery = setTimeout(() => fixture.commands.push({ action: 'unexpected-test-command' }), 20)
  try {
    await assert.rejects(assertNoCommands(fixture, 0, 'Delayed command must fail'), /Delayed command must fail/)
  } finally { clearTimeout(delivery) }
})

test('candidate receipt paths resolve portable POSIX paths under a fixed checkout', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'browser-candidate-path-'))
  try {
    fs.mkdirSync(path.join(root, 'browser-candidate-web', 'frontend'), { recursive: true })
    fs.writeFileSync(path.join(root, 'browser-candidate-web', 'manifest.json'), '{}')
    for (const relative of ['browser-candidate-web/frontend', 'browser-candidate-web/manifest.json']) {
      assert.equal(candidatePath(relative, root), path.join(fs.realpathSync(root), ...relative.split('/')))
    }
    for (const invalid of ['', '/tmp/frontend', 'C:/frontend', 'C:\\frontend', '../frontend', 'browser-candidate-web/../frontend', 'browser-candidate-web/./frontend', 'browser-candidate-web//frontend', 'browser-candidate-web/frontend/']) {
      assert.throws(() => candidatePath(invalid, root), 'Noncanonical or escaping candidate paths must fail')
    }
    fs.symlinkSync(path.join(root, 'browser-candidate-web', 'frontend'), path.join(root, 'linked-frontend'), 'dir')
    assert.throws(() => candidatePath('linked-frontend', root), 'Links must fail even if their target is inside the checkout')
  } finally { fs.rmSync(root, { recursive: true, force: true }) }
})

test('served candidate bytes must match the prepared inventory independently of its manifest', () => {
  const inventory = { 'index.html': 'a'.repeat(64), 'assets/main.js': 'b'.repeat(64) }
  const measured = new Map(Object.entries(inventory).reverse())
  verifyCandidateInventory({ frontendFiles: inventory }, measured)
  for (const mismatch of [
    { ...inventory, 'assets/main.js': 'c'.repeat(64) },
    { 'index.html': inventory['index.html'] },
    { ...inventory, 'assets/extra.js': 'd'.repeat(64) },
  ]) assert.throws(() => verifyCandidateInventory({ frontendFiles: inventory }, new Map(Object.entries(mismatch))), /Served frontend bytes differ/)
  for (const malformed of [undefined, null, [], 'inventory']) {
    assert.throws(() => verifyCandidateInventory({ frontendFiles: malformed }, measured), /inventory is required/)
  }
})

test('adapter enforces prepared byte inventory before loading any browser toolchain', () => {
  const root = path.resolve(__dirname, '..')
  const application = JSON.parse(fs.readFileSync(path.join(root, '.github/browser-application.json'), 'utf8'))
  const manifest = JSON.parse(fs.readFileSync(path.join(root, application.manifest), 'utf8'))
  const inventory = application.manifestKind === 'python'
    ? Object.fromEntries(manifest.files.map(file => [file.path, file.sha256])) : manifest.sha256
  const diagnostics = fs.mkdtempSync(path.join(os.tmpdir(), 'browser-byte-binding-'))
  try {
    const candidate = {
      artifactId: 1, manifestKind: application.manifestKind, releaseReceipt: true,
      sourceSha: 'f'.repeat(40), archiveSha256: 'f'.repeat(64), ociSha256: 'f'.repeat(64), receiptSha256: 'f'.repeat(64),
      distribution: application.distribution, manifestPath: application.manifest, frontendFiles: inventory,
    }
    const environment = { ...process.env, CI_BROWSER_CANDIDATE_ARTIFACT_ID: '1', CI_BROWSER_CANDIDATE_ROOT: path.posix.dirname(application.distribution), CI_BROWSER_DIAGNOSTICS: diagnostics }
    delete environment.CI_PLAYWRIGHT_MODULE
    const probe = () => {
      fs.writeFileSync(path.join(diagnostics, 'candidate.json'), JSON.stringify(candidate))
      return spawnSync(process.execPath, [path.join(__dirname, 'browser-acceptance.cjs')], { cwd: root, env: environment, encoding: 'utf8', timeout: 15000, maxBuffer: 100000 })
    }
    const accepted = probe()
    assert.equal(accepted.status, 1)
    assert.match(accepted.stdout, /Verified embedded frontend inventory/)
    assert.match(accepted.stderr, /Set CI_PLAYWRIGHT_MODULE/)
    const first = Object.keys(inventory)[0]
    candidate.frontendFiles = { ...inventory, [first]: inventory[first][0] === 'a' ? 'b'.repeat(64) : 'a'.repeat(64) }
    const rejected = probe()
    assert.equal(rejected.status, 1)
    assert.match(rejected.stderr, /Served frontend bytes differ from the prepared candidate/)
    assert.doesNotMatch(rejected.stdout, /Verified embedded frontend inventory/)
  } finally { fs.rmSync(diagnostics, { recursive: true, force: true }) }
})
