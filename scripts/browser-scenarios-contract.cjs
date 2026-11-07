/** Exact application contract, in addition to the shared runner's generic verdict. */
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const bytes = fs.readFileSync(path.join(__dirname, 'browser-scenarios.json'))
const contract = JSON.parse(bytes)
assert.equal(contract.schema, 1)
assert.equal(typeof contract.id, 'string')
assert(Array.isArray(contract.scenarios) && contract.scenarios.length > 0)
assert(contract.scenarios.every(name => typeof name === 'string' && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)))
assert.equal(new Set(contract.scenarios).size, contract.scenarios.length, 'Contract names must be unique')
function verifyScenarioContract(result) {
  assert(Array.isArray(result.scenarios), 'Scenario evidence is required')
  assert.equal(result.testsFailed, 0, 'Failed scenarios cannot qualify')
  assert.equal(result.testsPassed, contract.scenarios.length, 'Required scenario count differs')
  assert.equal(new Set(result.scenarios).size, result.scenarios.length, 'Duplicate scenario evidence')
  assert.deepEqual(new Set(result.scenarios), new Set(contract.scenarios), 'Required application scenarios differ')
  return { id: contract.id, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), required: contract.scenarios.length }
}
module.exports = { contract, verifyScenarioContract }

if (require.main === module) {
  assert(process.argv.length > 2, 'Pass one or more browser result JSON files to verify')
  for (const file of process.argv.slice(2)) {
    const result = JSON.parse(fs.readFileSync(file, 'utf8'))
    const verified = verifyScenarioContract(result)
    assert.deepEqual(result.scenarioContract, verified, 'Evidence must bind this exact contract revision')
    console.log(`PASS ${file}: ${verified.id}, ${verified.required} required scenarios`)
  }
}
