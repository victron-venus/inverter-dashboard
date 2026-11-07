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
