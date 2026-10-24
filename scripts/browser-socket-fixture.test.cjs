/** Exercise the mocked close handshake and reload with the selected real browser. */
const assert = require('node:assert/strict')
const { trackMockSocket } = require('./browser-socket-fixture.cjs')

async function runSocketCloseControl(browser, origin) {
  const context = await browser.newContext({ serviceWorkers: 'block' })
  context.setDefaultTimeout(10000)
  context.setDefaultNavigationTimeout(30000)
  const sockets = new Set()
  let connections = 0
  try {
    await context.route('**/*', route => {
      assert.equal(new URL(route.request().url()).origin, origin, 'Socket control must remain local')
      return route.fulfill({ contentType: 'text/html', body: `<!doctype html><title>Socket close control</title><script>
        window.messages = [];
        window.closeEvents = [];
        const url = new URL('/ws', location.href);
        url.protocol = 'ws:';
        window.socket = new WebSocket(url);
        socket.onopen = () => socket.send('ready');
        socket.onmessage = event => messages.push(event.data);
        socket.onclose = event => closeEvents.push({ code: event.code, reason: event.reason });
      </script>` })
    })
    await context.routeWebSocket('**/*', socket => {
      const url = new URL(socket.url())
      assert.equal(url.host, new URL(origin).host)
      assert.equal(url.pathname, '/ws')
      connections++
      trackMockSocket(socket, sockets)
      socket.onMessage(message => socket.send(message))
    })
    const page = await context.newPage()
    await page.goto(origin, { waitUntil: 'load' })
    for (const options of [{ code: 1000, reason: 'ready to reload' }, {}]) {
      await page.waitForFunction(() => socket.readyState === WebSocket.OPEN && messages.includes('ready'))
      assert.equal(sockets.size, 1)
      await page.evaluate(options => socket.close(options.code, options.reason), options)
      await page.waitForFunction(() => socket.readyState === WebSocket.CLOSED && closeEvents.length === 1)
      assert.equal(sockets.size, 0, 'Closed sockets must be removed from broadcast targets')
      if (options.code === 1000) {
        assert.deepEqual(await page.evaluate(() => closeEvents[0]), options)
        await page.reload({ waitUntil: 'load' })
      }
    }
    assert.equal(connections, 2, 'Reload must establish a fresh working socket')
    console.log('PASS socket close handshake and reload reconnect')
    return { mode: 'socket-close-and-reload', expectedRejection: false, passed: true }
  } finally {
    await context.close()
  }
}

module.exports = { runSocketCloseControl }
