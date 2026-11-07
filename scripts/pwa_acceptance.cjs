#!/usr/bin/env node
/** Isolated HTTPS, real candidate worker, synthetic PushManager/provider boundary.
 * This is engine regression evidence. It never qualifies native OS delivery.
 */
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const https = require('node:https')
const os = require('node:os')
const path = require('node:path')
const { execFileSync } = require('node:child_process')
const { setTimeout: delay } = require('node:timers/promises')

const SCENARIOS = Object.freeze([
  'secure-context-manifest-and-exact-worker',
  'startup-does-not-request-permission-or-subscribe',
  'denied-permission-does-not-register',
  'granted-permission-registers-ui-fixture-subscription',
  'worker-generation-rejects-stale-enable',
  'worker-update-preserves-revoked-settings',
  'disable-removes-fixture-and-browser-subscription',
  'worker-does-not-cache-assets-or-handle-offline-controls',
  'worker-unregister-removes-registration',
])
const sha = data => crypto.createHash('sha256').update(data).digest('hex')

// The trusted checkout is code-derived. Only pure unit controls may supply a root.
const TRUSTED_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'
function workspacePath(value, root = path.resolve(__dirname, '..')) {
  assert(typeof value === 'string' && value && !value.includes('\\') && !value.split('/').includes('..'), 'Invalid qualification path')
  const anchor = path.resolve(root)
  const trusted = fs.realpathSync(anchor)
  const supplied = path.isAbsolute(value) ? value : path.join(anchor, value)
  const relative = path.relative(anchor, supplied)
  assert(relative && relative !== '..' && !relative.startsWith('../') && !path.isAbsolute(relative), 'Qualification path escapes trusted checkout')
  let current = trusted
  for (const part of relative.split(path.sep)) {
    current = path.join(current, part)
    const entry = fs.lstatSync(current, { throwIfNoEntry: false })
    if (!entry) break
    assert(!entry.isSymbolicLink(), 'Linked qualification path rejected')
    assert(entry.isDirectory() || entry.isFile(), 'Qualification path must be regular')
    if (current !== path.join(trusted, relative)) assert(entry.isDirectory(), 'Qualification parent must be a directory')
  }
  return path.join(trusted, relative)
}

function candidatePath(value, root) {
  assert(typeof value === 'string' && value && !path.isAbsolute(value) && !value.includes('\\') && !value.includes(':') && value.split('/').every(part => part && part !== '.' && part !== '..'), 'Candidate path must be canonical checkout-relative POSIX')
  return workspacePath(value, root)
}

function loadCandidate(filename, workspaceRoot) {
  filename = workspacePath(filename, workspaceRoot)
  const candidate = JSON.parse(fs.readFileSync(filename, 'utf8'))
  assert.match(candidate.repository, /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/)
  assert.match(candidate.sourceSha, /^[a-f0-9]{40}$/)
  assert.match(candidate.candidateSha256, /^[a-f0-9]{64}$/)
  for (const key of ['runId', 'runAttempt', 'candidateArtifactId']) assert(Number.isSafeInteger(candidate[key]) && candidate[key] > 0, key)
  if (candidate.artifactId !== undefined) assert.equal(candidate.artifactId, candidate.candidateArtifactId)
  if (candidate.archiveSha256 !== undefined) assert.equal(candidate.archiveSha256, candidate.candidateSha256)
  const root = candidatePath(candidate.distribution, workspaceRoot)
  const assets = new Map()
  const inventory = candidate.frontendFiles
  assert(inventory && Object.keys(inventory).length, 'Candidate frontend inventory required')
  for (const [name, hash] of Object.entries(inventory)) {
    assert(name && !name.includes('\\') && !name.split('/').some(part => !part || part === '.' || part === '..') && !path.isAbsolute(name), 'Invalid frontend path')
    const filename = path.join(root, name)
    for (let probe = filename; probe !== root; probe = path.dirname(probe)) assert(!fs.lstatSync(probe).isSymbolicLink(), 'Linked frontend rejected')
    assert(fs.statSync(filename).isFile(), 'Regular frontend file required')
    const data = fs.readFileSync(filename)
    assert.match(hash, /^[a-f0-9]{64}$/)
    assert.equal(sha(data), hash, `Candidate bytes changed: ${name}`)
    assets.set('/' + name, data)
  }
  for (const file of ['/index.html', '/notifications-sw.js', '/manifest.webmanifest']) assert(assets.has(file), `Missing ${file}`)
  return { candidate, assets }
}

function binding(candidate) {
  return Object.fromEntries(['repository', 'sourceSha', 'runId', 'runAttempt', 'candidateArtifactId', 'candidateSha256'].map(key => [key, candidate[key]]))
}

async function workerRequest(page, request) {
  return page.evaluate(async value => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    if (!registration?.active) throw new Error('Worker not active')
    return new Promise((resolve, reject) => {
      const channel = new MessageChannel()
      const timer = setTimeout(() => { channel.port1.close(); reject(new Error('Worker response timeout')) }, 5000)
      channel.port1.onmessage = event => {
        clearTimeout(timer); channel.port1.close()
        if (event.data?.ok === true) resolve(event.data.result)
        else reject(new Error('Worker request rejected'))
      }
      registration.active.postMessage(value, [channel.port2])
    })
  }, request)
}

async function waitFor(read, test, label, remaining = 100) {
  const value = await read()
  if (test(value)) return value
  assert(remaining > 1, `Timed out: ${label}`)
  // This is bounded state polling; observations must remain sequential.
  await delay(100)
  return waitFor(read, test, label, remaining - 1)
}

function requestBody(req) {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', value => { body += value; if (body.length > 20000) { req.destroy(); reject(new Error('Body limit')) } })
    req.on('end', () => { try { resolve(body ? JSON.parse(body) : {}) } catch (error) { reject(error) } })
    req.on('error', reject)
  })
}

async function setNotificationPermission(session, origin, setting, isolatedPersistent = false) {
  const { targetInfo } = await session.send('Target.getTargetInfo')
  assert(targetInfo.browserContextId || isolatedPersistent, 'Permission override requires an isolated context')
  const identity = targetInfo.browserContextId ? { browserContextId: targetInfo.browserContextId } : {}
  await session.send('Browser.setPermission', { permission: { name: 'notifications' }, setting, origin, ...identity })
  // Keep this CDP session attached for the test: detaching resets its overrides.
}

function fixtureCertificate(directory) {
  const key = path.join(directory, 'key.pem')
  const cert = path.join(directory, 'cert.pem')
  const config = path.join(directory, 'openssl.cnf')
  fs.writeFileSync(config, '[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=v3\n[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n')
  execFileSync('/usr/bin/openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-config', config, '-keyout', key, '-out', cert], { stdio: 'ignore', timeout: 30000, env: { PATH: TRUSTED_PATH } })
  return { key, cert }
}

async function main() {
  assert.equal(process.env.GITHUB_ACTIONS, 'true', 'Browser execution is hosted-only; local UI must use cua_repl')
  const candidatePath = workspacePath(process.env.CI_PWA_CANDIDATE)
  const modulePath = workspacePath(process.env.CI_PLAYWRIGHT_MODULE)
  assert(candidatePath && path.isAbsolute(candidatePath), 'CI_PWA_CANDIDATE must name the verified candidate receipt')
  assert(modulePath && path.isAbsolute(modulePath), 'CI_PLAYWRIGHT_MODULE must name the isolated installed toolchain')
  const { candidate, assets } = loadCandidate(candidatePath)
  const engine = process.env.CI_BROWSER_ENGINE || 'chromium'
  assert.equal(engine, 'chromium', 'This permission-override lane currently qualifies Chromium only')
  const output = workspacePath(process.env.CI_PWA_OUTPUT || '.compatibility/pwa')
  fs.mkdirSync(output, { recursive: true })
  const resultPath = workspacePath(path.join(output, 'result.json'))
  const screenshotPath = workspacePath(path.join(output, 'pwa-settings.png'))
  const tracePath = workspacePath(path.join(output, 'trace.zip'))
  fs.rmSync(resultPath, { force: true })
  const startedAt = new Date().toISOString()
  const scenarios = []
  const failures = []
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-pwa-'))
  let server, browser, context
  const requests = []
  const commands = []
  const external = []
  const fixture = { registered: false, deleted: false, posts: 0 }
  const keys = crypto.createECDH('prime256v1'); keys.generateKeys()
  const publicKey = keys.getPublicKey().toString('base64url')
  const preferences = { native: true, ev: true, water: true, lowBattery: true }
  const state = {
    data_source: 'mqtt', native_connected: true, mqtt_connected: true, dry_run: false,
    controller_controls_available: true, controller_observed_at: Date.now() / 1000,
    battery_soc: 73, gt: 420, tt: 850, solar_total: 1200, loads: {}, features: { ha: false },
    telemetry: { quality: 'live', source: 'mqtt', observed_at: new Date().toISOString() },
    ui_config: { settings: { mqtt_port: 1883, show_active_loads: true } },
  }
  const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.webmanifest': 'application/manifest+json', '.json': 'application/json', '.woff2': 'font/woff2', '.png': 'image/png', '.ico': 'image/x-icon' }
  const run = async (id, action) => {
    await action()
    scenarios.push({ id, status: 'passed', observedAt: new Date().toISOString() })
    console.log(`PASS ${id}`)
  }
  try {
    // Disposable fixture trust only; never change the host trust store.
    fixtureCertificate(temp)
    server = https.createServer({ key: fs.readFileSync(path.join(temp, 'key.pem')), cert: fs.readFileSync(path.join(temp, 'cert.pem')) }, async (req, res) => {
      try {
        const route = new URL(req.url, 'https://localhost').pathname
        requests.push({ path: route, method: req.method })
        res.setHeader('Cache-Control', 'no-cache')
        res.setHeader('Service-Worker-Allowed', '/')
        res.setHeader('Content-Type', 'application/json')
        if (route === '/api/state') return res.end(JSON.stringify(state))
        if (route === '/api/notifications/status') return res.end(JSON.stringify({ enabled: true, available: true, reason: null, publicKey, preferencesDefaults: preferences, maxNotificationAgeSeconds: 300 }))
        if (route === '/api/notifications/subscription/status') return res.end(JSON.stringify({ registered: fixture.registered, preferences }))
        if (route === '/api/notifications/subscription') {
          const body = await requestBody(req)
          if (req.method === 'POST') {
            assert(body.subscription?.endpoint === 'https://push.invalid/isolated-fixture', 'Only fixture subscriptions allowed')
            fixture.registered = true; fixture.posts++
          } else if (req.method === 'DELETE') {
            assert.equal(body.endpoint, 'https://push.invalid/isolated-fixture')
            fixture.registered = false; fixture.deleted = true
          } else { res.writeHead(405); return res.end('{}') }
          return res.end(JSON.stringify({ registered: fixture.registered, preferences }))
        }
        if (route.startsWith('/api/notifications/')) return res.end(JSON.stringify({ items: [], notifications: [] }))
        const assetPath = route === '/' ? '/index.html' : route
        if (!assets.has(assetPath)) { res.writeHead(404); return res.end('{}') }
        res.setHeader('Content-Type', mime[path.extname(assetPath)] || 'application/octet-stream')
        res.end(assets.get(assetPath))
      } catch { res.writeHead(500); res.end('{}') }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `https://127.0.0.1:${server.address().port}`
    context = await require(modulePath).chromium.launchPersistentContext(path.join(temp, 'profile'), { channel: 'chromium', headless: true, args: ['--ignore-certificate-errors'], ignoreHTTPSErrors: true, serviceWorkers: 'allow', locale: 'en-US' })
    browser = context.browser()
    context.setDefaultTimeout(15000)
    await context.tracing.start({ screenshots: true, snapshots: true })
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue()
      external.push(new URL(route.request().url()).origin)
      return route.abort('blockedbyclient')
    })
    await context.routeWebSocket('**/*', ws => {
      const url = new URL(ws.url())
      if (url.host !== new URL(origin).host || url.pathname !== '/ws') { external.push(url.origin); ws.close(); return }
      ws.onMessage(raw => { commands.push(String(raw)); ws.close() })
      ws.send(JSON.stringify(state))
    })
    // The provider boundary is deliberately fake; SW, IndexedDB, UI and HTTPS are real.
    await context.addInitScript(() => {
      let subscription = null
      window.__pwaSubscriptionCalls = 0
      window.__pwaPermissionRequests = 0
      const requestPermission = Notification.requestPermission.bind(Notification)
      Notification.requestPermission = (...args) => { window.__pwaPermissionRequests++; return requestPermission(...args) }
      Object.defineProperty(PushManager.prototype, 'getSubscription', { value: async () => subscription })
      Object.defineProperty(PushManager.prototype, 'subscribe', { value: async options => {
        window.__pwaSubscriptionCalls++
        if (Notification.permission !== 'granted') throw new DOMException('Permission denied', 'NotAllowedError')
        subscription = {
          endpoint: 'https://push.invalid/isolated-fixture', options,
          toJSON: () => ({ endpoint: 'https://push.invalid/isolated-fixture', keys: { p256dh: 'fixture', auth: 'fixture' } }),
          unsubscribe: async () => { subscription = null; return true },
        }
        return subscription
      } })
    })
    const page = await context.newPage()
    const permissionSession = await context.newCDPSession(page)
    await setNotificationPermission(permissionSession, origin, 'prompt', true)
    await page.goto(origin, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    const settings = page.getByRole('region', { name: 'System notifications', exact: true })
    await settings.waitFor({ state: 'visible' })
    await run(SCENARIOS[0], async () => {
      assert.equal(await page.evaluate(() => isSecureContext), true)
      const manifest = await page.evaluate(async () => (await fetch('/manifest.webmanifest')).json())
      assert.equal(manifest.display, 'standalone'); assert.equal(manifest.start_url, '/'); assert.equal(manifest.scope, '/')
      const body = await page.evaluate(async () => Array.from(new Uint8Array(await (await fetch('/notifications-sw.js')).arrayBuffer())))
      assert.equal(sha(Buffer.from(body)), candidate.frontendFiles['notifications-sw.js'])
    })
    await run(SCENARIOS[1], async () => {
      const permission = await page.evaluate(() => Notification.permission)
      assert(['default', 'denied'].includes(permission), 'A fresh isolated context must not start granted')
      assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0)
      assert.equal(fixture.posts, 0)
      assert.equal(await page.evaluate(() => window.__pwaPermissionRequests), 0)
      assert.equal(await page.evaluate(() => window.__pwaSubscriptionCalls), 0)
    })
    await run(SCENARIOS[2], async () => {
      await setNotificationPermission(permissionSession, origin, 'denied', true)
      await settings.getByRole('button', { name: 'Refresh status', exact: true }).click()
      await settings.getByText('Blocked by browser permission', { exact: true }).waitFor()
      assert(await settings.getByRole('button', { name: 'Enable', exact: true }).isDisabled())
      assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0)
      assert.equal(await page.evaluate(() => window.__pwaSubscriptionCalls), 0)
      assert.equal(fixture.posts, 0)
    })
    await run(SCENARIOS[3], async () => {
      await setNotificationPermission(permissionSession, origin, 'granted', true)
      assert.equal(await page.evaluate(() => Notification.permission), 'granted')
      await settings.getByRole('button', { name: 'Refresh status', exact: true }).click()
      await settings.getByRole('button', { name: 'Enable', exact: true }).click()
      await settings.getByText('Enabled — server registration confirmed', { exact: true }).waitFor()
      assert.equal(fixture.registered, true)
      const details = await page.evaluate(async () => { const r = await navigator.serviceWorker.ready; return { scope: r.scope, script: r.active.scriptURL, cache: r.updateViaCache } })
      assert.deepEqual(details, { scope: `${origin}/`, script: `${origin}/notifications-sw.js`, cache: 'none' })
    })
    await run(SCENARIOS[4], async () => {
      const before = await workerRequest(page, { type: 'notification-settings-get' })
      const revoked = await workerRequest(page, { type: 'notification-settings-set', enabled: false })
      const stale = await workerRequest(page, { type: 'notification-settings-set', enabled: true, generation: before.generation })
      assert.equal(revoked.enabled, false); assert.equal(stale.applied, false); assert.equal(stale.enabled, false)
    })
    await run(SCENARIOS[5], async () => {
      const before = await workerRequest(page, { type: 'notification-settings-get' })
      await page.evaluate(async () => { const r = await navigator.serviceWorker.ready; await r.update() })
      assert.deepEqual(await workerRequest(page, { type: 'notification-settings-get' }), before)
      // Identical-candidate update check. A distinct previous-version upgrade is native acceptance.
      assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.ready).updateViaCache), 'none')
    })
    await run(SCENARIOS[6], async () => {
      await settings.getByRole('button', { name: 'Disable', exact: true }).click()
      await waitFor(() => fixture.deleted, value => value === true, 'server fixture deletion')
      assert.equal(fixture.registered, false)
      assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.ready).pushManager.getSubscription()), null)
      assert.equal((await workerRequest(page, { type: 'notification-settings-get' })).enabled, false)
    })
    await run(SCENARIOS[7], async () => {
      assert.equal(await page.evaluate(async () => (await caches.keys()).length), 0)
      await context.setOffline(true)
      assert.equal(await page.evaluate(async () => { try { await fetch('/api/state', { cache: 'no-store' }); return false } catch { return true } }), true)
      await context.setOffline(false)
      assert.equal(requests.filter(item => item.path === '/api/command').length, 0)
    })
    await run(SCENARIOS[8], async () => {
      assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.ready).unregister()), true)
      assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0)
    })
    assert.deepEqual(external, [])
    assert.deepEqual(commands, [], 'PWA lifecycle must not issue actuator/settings commands')
    assert.deepEqual(scenarios.map(item => item.id), SCENARIOS)
    await page.screenshot({ path: screenshotPath, fullPage: true })
  } catch (error) {
    failures.push({ message: String(error.stack || error) })
    process.exitCode = 1
  } finally {
    if (context) await context.tracing.stop({ path: tracePath }).catch(() => {})
    const browserVersion = browser?.version() || 'not-started'
    // launchPersistentContext owns the browser lifetime, even if browser() is unavailable.
    if (context) {
      try { await context.close() }
      catch (error) { failures.push({ message: `Persistent context cleanup failed: ${error.message}` }); process.exitCode = 1 }
    }
    if (server) await new Promise(resolve => server.close(resolve))
    fs.rmSync(temp, { recursive: true, force: true })
    fs.writeFileSync(resultPath, JSON.stringify({
      schemaVersion: 1, scope: 'engine-pwa-isolated', candidate: binding(candidate),
      startedAt, finishedAt: new Date().toISOString(), engine, browserVersion,
      status: failures.length ? 'failed' : 'passed', scenarios, failures,
      serviceWorkers: 'allow', tlsTrust: 'isolated-self-signed', browserContextMode: 'isolated-persistent-profile',
      permissionMechanism: 'browser-test-override', subscriptionMechanism: 'isolated-ui-fixture',
      osNotificationObserved: false, nativeQualified: false, previousVersionUpgradeQualified: false,
    }, null, 2) + '\n')
  }
}

module.exports = { SCENARIOS, sha, loadCandidate, binding, fixtureCertificate, setNotificationPermission, workspacePath, candidatePath, TRUSTED_PATH, waitFor }
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1 })
