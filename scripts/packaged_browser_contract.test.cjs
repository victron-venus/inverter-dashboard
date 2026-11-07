/** Exercise the final command checker through real isolated loopback requests. */
const assert = require('node:assert/strict')
const http = require('node:http')
const test = require('node:test')
const { expectedCommandJournal, verifyFinalCommandJournal } = require('./packaged_browser_contract.cjs')

async function journalFixture(t) {
  const capture = []
  const routes = new Map([['/v1/commands/toggle', 'igw'], ['/api/services/switch/toggle', 'ha']])
  const server = http.createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json')
    if (request.method === 'GET' && request.url === '/fixture/status') {
      response.end(JSON.stringify({ capture }))
      return
    }
    if (request.method !== 'POST' || !routes.has(request.url)) {
      response.writeHead(404).end('{}')
      return
    }
    try {
      let body = ''
      for await (const chunk of request) {
        body += chunk.toString('utf8')
        assert(body.length <= 4096, 'Fixture request exceeds its bound')
      }
      capture.push({ transport: routes.get(request.url), path: request.url, body: JSON.parse(body) })
      response.end('{}')
    } catch {
      response.writeHead(400).end('{}')
    }
  })
  server.requestTimeout = 2000
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  t.after(async () => {
    await new Promise((resolve, reject) => {
      server.close(error => error ? reject(error) : resolve())
      server.closeAllConnections()
    })
  })
  const origin = `http://127.0.0.1:${server.address().port}`
  const publish = async (route, body) => {
    const response = await fetch(origin + route, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(2000),
    })
    assert.equal(response.status, 200)
    await response.json()
  }
  const read = async () => {
    const response = await fetch(origin + '/fixture/status', { signal: AbortSignal.timeout(2000) })
    assert.equal(response.status, 200)
    return response.json()
  }
  return { publish, read }
}

const controllerInput = { action: 'toggle', entity: 'charge_battery', state: true }
const homeInput = { action: 'toggle', entity: 'switch.fixture_light' }

async function publishAllowed(publish) {
  await publish('/v1/commands/toggle', { entity: 'charge_battery', state: 'on' })
  await publish('/api/services/switch/toggle', { entity_id: 'switch.fixture_light' })
}

test('final journal rejects a duplicate published after the initial check', { timeout: 10000 }, async t => {
  const { publish, read } = await journalFixture(t)
  const expected = expectedCommandJournal('igw', controllerInput, homeInput)
  await publishAllowed(publish)
  await verifyFinalCommandJournal(read, expected)
  // The initial routes-once check has passed. A later duplicate must invalidate it.
  await publish('/v1/commands/toggle', { entity: 'charge_battery', state: 'on' })
  await assert.rejects(verifyFinalCommandJournal(read, expected), /Unexpected late, duplicate/)
  assert.equal((await read()).capture.length, 3)
})

test('unsolicited command before the first capture cannot become an expected baseline', { timeout: 10000 }, async t => {
  const { publish, read } = await journalFixture(t)
  const expected = expectedCommandJournal('igw', controllerInput, homeInput)
  await publish('/v1/commands/toggle', { entity: 'charge_battery', state: 'off' })
  await publishAllowed(publish)
  // No journal has been read yet. Expected publications come only from test inputs.
  await assert.rejects(verifyFinalCommandJournal(read, expected), /Unexpected late, duplicate/)
  assert.equal((await read()).capture.length, 3)
})

test('expected MQTT and HA publications use explicit sent inputs and fixed routes', () => {
  assert.deepEqual(expectedCommandJournal('mqtt', { action: 'setpoint', value: 317 }, homeInput), [
    { transport: 'mqtt', path: 'inverter/cmd/setpoint', body: { value: 317 } },
    { transport: 'ha', path: '/api/services/switch/toggle', body: { entity_id: 'switch.fixture_light' } },
  ])
  assert.throws(() => expectedCommandJournal('other', controllerInput, homeInput), /Unexpected test transport/)
})
