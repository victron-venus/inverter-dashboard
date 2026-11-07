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

async function verifyFinalCommandJournal(readFixture, expectedCaptures) {
  const final = await readFixture()
  assert.deepEqual(final.capture, expectedCaptures,
    'Unexpected late, duplicate or unconfigured command in the final fixture journal')
}

module.exports = { expectedCommandJournal, verifyFinalCommandJournal }
