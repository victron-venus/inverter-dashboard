/** Exercise the final command checker through real isolated loopback requests. */
const assert = require('node:assert/strict')
const http = require('node:http')
const test = require('node:test')
const { verifyFinalCommandJournal } = require('./packaged_browser_contract.cjs')

test('final journal rejects a duplicate published after the initial check', { timeout: 10000 }, async t => {
  const capture = []
  const server = http.createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json')
    if (request.method === 'GET' && request.url === '/fixture/status') {
      response.end(JSON.stringify({ capture }))
      return
    }
    if (request.method !== 'POST' || request.url !== '/v1/commands/toggle') {
      response.writeHead(404).end('{}')
      return
    }
    try {
      let body = ''
      for await (const chunk of request) {
        body += chunk.toString('utf8')
        assert(body.length <= 4096, 'Fixture request exceeds its bound')
      }
      capture.push({ transport: 'igw', path: request.url, body: JSON.parse(body) })
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
  const command = { entity: 'charge_battery', state: 'on' }
  const expected = [{ transport: 'igw', path: '/v1/commands/toggle', body: command }]
  const publish = async () => {
    const response = await fetch(origin + '/v1/commands/toggle', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(command), signal: AbortSignal.timeout(2000),
    })
    assert.equal(response.status, 200)
    await response.json()
  }
  const read = async () => {
    const response = await fetch(origin + '/fixture/status', { signal: AbortSignal.timeout(2000) })
    assert.equal(response.status, 200)
    return response.json()
  }
  await publish()
  await verifyFinalCommandJournal(read, expected)
  // The initial routes-once check has passed. A later duplicate must invalidate it.
  await publish()
  await assert.rejects(verifyFinalCommandJournal(read, expected), /Unexpected late, duplicate/)
  assert.equal((await read()).capture.length, 2)
})
