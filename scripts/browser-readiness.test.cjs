#!/usr/bin/env node
/** Real-browser controls for the acceptance harness; no production endpoints. */
const assert = require('node:assert/strict')
const http = require('node:http')
const path = require('node:path')
const { waitForHydratedDashboard } = require('./browser-readiness.cjs')

const modulePath = process.env.CI_PLAYWRIGHT_MODULE
assert(modulePath && path.isAbsolute(modulePath), 'Set CI_PLAYWRIGHT_MODULE to the isolated toolchain')
const playwright = require(modulePath)
const engine = process.env.CI_BROWSER_ENGINE || 'chromium'
const channel = process.env.CI_BROWSER_CHANNEL || ''
assert(['chromium', 'firefox', 'webkit'].includes(engine), 'Unsupported browser engine')
const pending = new Map()
const waitingStates = new Map()
let reverseStateObserved
const reverseStateArrival = new Promise(resolve => { reverseStateObserved = resolve })
function flushState(mode) {
  if (![...pending.values()].includes(mode)) return
  for (const [response, waitingMode] of waitingStates) {
    if (waitingMode !== mode) continue
    waitingStates.delete(response)
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({ soc: mode === 'wrong-soc' ? 41 : mode === 'public' || mode === 'missing-public' ? 61 : 42 }))
  }
}
// Request data selects a precomputed page; it is never interpolated into HTML.
const fixturePages = new Map([
  'ready', 'reverse-arrival', 'missing-js', 'stalled', 'wrong-soc', 'public', 'missing-public', 'disconnected',
].map(mode => [mode, `<!doctype html><title>Readiness control</title><script defer src="/app.js?mode=${mode}"></script>`]))
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost')
  const mode = url.searchParams.get('mode')
  res.setHeader('Cache-Control', 'no-store')
  if (url.pathname === '/noncritical' || (url.pathname === '/state' && mode === 'stalled')) {
    pending.set(res, mode)
    res.on('close', () => pending.delete(res))
    if (url.pathname === '/noncritical') flushState(mode)
    return
  }
  if (url.pathname === '/state') {
    waitingStates.set(res, mode)
    res.on('close', () => waitingStates.delete(res))
    if (mode === 'reverse-arrival') reverseStateObserved()
    flushState(mode)
    return
  }
  if (url.pathname === '/app.js') {
    if (mode === 'missing-js') { res.writeHead(404).end(); return }
    res.setHeader('Content-Type', 'text/javascript; charset=utf-8')
    res.end(`
      const mode = new URL(location.href).searchParams.get('mode');
      if (mode !== 'reverse-arrival') fetch('/noncritical?mode=' + mode);
      fetch('/state?mode=' + mode).then(response => response.json()).then(state => {
        const soc = document.createElement('span');
        soc.textContent = state.soc + '%';
        document.body.append(soc);
        const status = document.createElement('span');
        status.dataset.testid = 'transport-status';
        status.dataset.connected = mode === 'disconnected' ? 'false' : 'true';
        status.textContent = mode === 'public' ? 'PUBLIC · READ-ONLY' : 'Connected';
        document.body.append(status);
      });
    `)
    return
  }
  if (url.pathname === '/') {
    const html = fixturePages.get(mode)
    if (!html) { res.writeHead(404).end(); return }
    res.setHeader('Content-Type', 'text/html; charset=utf-8')
    res.end(html)
    return
  }
  res.writeHead(404).end()
})

async function runReadinessControls() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  let browser
  try {
    const unknownPage = await fetch(`http://127.0.0.1:${server.address().port}/?mode=%3Cscript%3E`)
    assert.equal(unknownPage.status, 404, 'Unknown fixture modes must not produce HTML')
    browser = await playwright[engine].launch({ headless: true, ...(channel ? { channel } : {}) })
    const cases = [
      { mode: 'ready', soc: 42 },
      { mode: 'reverse-arrival', soc: 42 },
      { mode: 'public', soc: 61, publicMode: true },
      { mode: 'missing-js', soc: 42, rejects: true },
      { mode: 'stalled', soc: 42, rejects: true },
      { mode: 'disconnected', soc: 42, rejects: true },
      { mode: 'wrong-soc', soc: 42, rejects: true },
      { mode: 'missing-public', soc: 61, publicMode: true, rejects: true },
    ]
    // Isolate held requests as well as DOM/storage: some browser network stacks
    // share a per-origin connection limit across otherwise fresh contexts.
    for (const { mode, soc, publicMode = false, rejects = false } of cases) {
      const context = await browser.newContext({ serviceWorkers: 'block' })
      context.setDefaultTimeout(10000)
      context.setDefaultNavigationTimeout(15000)
      const traffic = { mode, requested: [], completed: [], failed: [], browserErrors: [] }
      try {
        const blocked = []
        await context.route('**/*', route => {
          const url = new URL(route.request().url())
          if (url.origin === `http://127.0.0.1:${server.address().port}`) return route.continue()
          blocked.push(url.origin)
          return route.abort('blockedbyclient')
        })
        const page = await context.newPage()
        page.on('request', request => traffic.requested.push(new URL(request.url()).pathname))
        page.on('requestfinished', request => traffic.completed.push(new URL(request.url()).pathname))
        page.on('requestfailed', request => traffic.failed.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText }))
        page.on('pageerror', error => traffic.browserErrors.push(error.message))
        await page.goto(`http://127.0.0.1:${server.address().port}/?mode=${mode}`, { waitUntil: 'load' })
        if (mode === 'reverse-arrival') {
          let timer
          try {
            await Promise.race([
              reverseStateArrival,
              new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Reverse-order state request did not arrive')), 10000) }),
            ])
          } finally {
            clearTimeout(timer)
          }
          assert([...waitingStates.values()].includes(mode), 'State must wait for its own noncritical request')
          assert(![...pending.values()].includes(mode), 'Reverse-order control must start state first')
          await page.evaluate(() => { void fetch('/noncritical?mode=reverse-arrival') })
        }
        if (rejects) {
          await assert.rejects(waitForHydratedDashboard(page, soc, publicMode), { name: 'TimeoutError' })
        } else {
          await waitForHydratedDashboard(page, soc, publicMode)
          assert([...pending.values()].includes(mode), 'Readiness must pass while its noncritical request is outstanding')
        }
        assert.deepEqual(blocked, [], 'Control fixtures must remain local')
        console.log(`PASS readiness ${mode}${rejects ? ' rejected' : ' with pending traffic'}`)
      } catch (error) {
        console.error('FAIL readiness control', JSON.stringify({ ...traffic, pending: [...pending.values()], waitingStates: [...waitingStates.values()] }))
        throw error
      } finally {
        await context.close()
        for (const responses of [pending, waitingStates]) {
          for (const [response, responseMode] of responses) {
            if (responseMode !== mode) continue
            responses.delete(response)
            response.destroy()
          }
        }
      }
    }
    return cases.map(({ mode, rejects = false }) => ({ mode, expectedRejection: rejects, passed: true }))
  } finally {
    if (browser) await browser.close()
    for (const response of pending.keys()) response.destroy()
    for (const response of waitingStates.keys()) response.destroy()
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
  }
}

module.exports = { runReadinessControls }
if (require.main === module) runReadinessControls().catch(error => { console.error(error); process.exitCode = 1 })
