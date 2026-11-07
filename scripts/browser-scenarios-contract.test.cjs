const assert = require('node:assert/strict')
const test = require('node:test')
const { contract, verifyScenarioContract } = require('./browser-scenarios-contract.cjs')
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
