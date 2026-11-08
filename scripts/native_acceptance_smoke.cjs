#!/usr/bin/env node
/** Hosted macOS Safari smoke against exact candidate bytes; no OS delivery claim. */
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const https = require('node:https')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { execFileSync, spawn } = require('node:child_process')
const { setTimeout: delay } = require('node:timers/promises')
const { loadCandidate, measuredFrontend, fixtureCertificate, workspacePath, TRUSTED_PATH } = require('./pwa_acceptance.cjs')
const { verifiedSetupPython } = require('./native_acceptance_runtime.cjs')
const { createNetworkGuard, finalizeNativeReceipt } = require('./native_acceptance_network.cjs')

function websocketAccept(key) {
  assert(typeof key === 'string' && /^[A-Za-z0-9+/]{22}==$/.test(key), 'Invalid WebSocket client nonce')
  // RFC6455 sections 4.2.2/10.8 mandate SHA-1 here; it is not an integrity or authentication primitive.
  return crypto.createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
}

function certificateFingerprint(filename) {
  const fingerprint = new crypto.X509Certificate(fs.readFileSync(filename)).fingerprint256.replaceAll(':', '')
  assert.match(fingerprint, /^[A-Fa-f0-9]{64}$/)
  return fingerprint
}

function trustCleanupStatus(error) {
  const status = { code: null, exitStatus: null, signal: null }
  if (error?.code != null) status.code = 'other'
  if (error?.code === 'ETIMEDOUT') status.code = 'ETIMEDOUT'
  if (Number.isInteger(error?.status) && error.status >= 0 && error.status <= 255) status.exitStatus = error.status
  if (typeof error?.signal === 'string' && Object.hasOwn(os.constants.signals, error.signal)) status.signal = error.signal
  return status
}

function removeFixtureTrust(cert, fingerprint, invoke = execFileSync) {
  const failures = []
  const actions = [
    { args: ['remove-trusted-cert', '-d', cert], error: 'Temporary fixture admin trust cleanup failed' },
    { args: ['delete-certificate', '-Z', fingerprint, '/Library/Keychains/System.keychain'], error: 'Temporary fixture certificate cleanup failed' },
  ]
  for (const action of actions) {
    // Child output and Error.message can contain command arguments or environment
    // details. Retain only bounded exit primitives, never raw subprocess text.
    try { invoke('/usr/bin/sudo', ['/usr/bin/security', ...action.args], { stdio: 'ignore', timeout: 30000, env: { ...process.env, PATH: TRUSTED_PATH } }) }
    catch (error) { failures.push(`${action.error} ${JSON.stringify(trustCleanupStatus(error))}`) }
  }
  return failures
}

function websocketFrame(value) {
  const body = Buffer.from(JSON.stringify(value))
  assert(body.length < 65536)
  if (body.length < 126) return Buffer.concat([Buffer.from([0x81, body.length]), body])
  const header = Buffer.alloc(4); header[0] = 0x81; header[1] = 126; header.writeUInt16BE(body.length, 2)
  return Buffer.concat([header, body])
}

function run(command, args, timeoutMs = 180000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: 'inherit', env: { ...process.env, PATH: TRUSTED_PATH } })
    const timer = setTimeout(() => child.kill('SIGKILL'), timeoutMs)
    child.once('error', error => { clearTimeout(timer); reject(error) })
    child.once('exit', code => { clearTimeout(timer); if (code === 0) resolve(); else reject(new Error(`${command} failed or timed out: ${code}`)) })
  })
}

async function terminate(child) {
  if (child?.exitCode !== null || child?.signalCode !== null) return
  await new Promise(resolve => {
    const killTimer = setTimeout(() => child.kill('SIGKILL'), 5000)
    const deadline = setTimeout(resolve, 8000)
    child.once('exit', () => { clearTimeout(killTimer); clearTimeout(deadline); resolve() })
    child.kill('SIGTERM')
  })
  assert(child.exitCode !== null || child.signalCode !== null, 'safaridriver did not terminate')
}

function clientFrameReader(onFrame) {
  let pending = Buffer.alloc(0)
  return chunk => {
    pending = Buffer.concat([pending, chunk])
    assert(pending.length <= 131072, 'Client frame buffer limit')
    while (pending.length >= 2) {
      assert((pending[0] & 0x70) === 0 && (pending[1] & 0x80) !== 0, 'Invalid client WebSocket frame')
      const opcode = pending[0] & 15
      let size = pending[1] & 127, header = 2
      assert.notEqual(size, 127, 'Oversized client frame')
      if (size === 126) {
        if (pending.length < 4) return
        size = pending.readUInt16BE(2); header = 4
      }
      if (pending.length < header + 4 + size) return
      const mask = pending.subarray(header, header + 4)
      const payload = Buffer.from(pending.subarray(header + 4, header + 4 + size))
      for (let index = 0; index < payload.length; index++) payload[index] ^= mask[index % 4]
      pending = pending.subarray(header + 4 + size)
      onFrame(opcode, payload)
    }
  }
}

function closeFixtureServer(server, forceAfterMs = 5000, deadlineMs = 8000) {
  return new Promise((resolve, reject) => {
    let forceTimer, deadline
    const finish = error => {
      clearTimeout(forceTimer); clearTimeout(deadline)
      if (error) reject(error); else resolve()
    }
    forceTimer = setTimeout(() => {
      try { server.closeAllConnections() } catch (error) { finish(error) }
    }, forceAfterMs)
    deadline = setTimeout(() => finish(new Error('Fixture server did not close')), deadlineMs)
    try { server.close(finish) } catch (error) { finish(error) }
  })
}

async function waitForNativeDriver(driver, url, { timeoutMs = 10000, read = fetch, now = () => performance.now(), pause = delay } = {}) {
  const deadline = now() + timeoutMs
  while (true) {
    assert(driver.exitCode === null && driver.signalCode === null, 'safaridriver exited during startup')
    const remaining = deadline - now()
    assert(remaining > 0, 'Timed out: safaridriver did not start')
    const response = await read(url, { signal: AbortSignal.timeout(Math.max(1, Math.ceil(Math.min(2000, remaining)))) }).then(value => value.ok, () => false)
    assert(now() < deadline, 'Timed out: safaridriver did not start')
    if (response) return
    await pause(Math.min(100, Math.max(0, deadline - now())))
  }
}

async function unusedPort() {
  const server = net.createServer()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  return port
}

async function main() {
  assert.equal(process.env.GITHUB_ACTIONS, 'true', 'Hosted execution only; local UI must use cua_repl')
  assert.equal(process.env.RUNNER_OS, 'macOS', 'Native Safari smoke requires macOS')
  const python = verifiedSetupPython(process.env.CI_NATIVE_PYTHON, process.env.CI_NATIVE_PYTHON_VERSION)
  const candidatePath = workspacePath(process.env.CI_PWA_CANDIDATE)
  assert(candidatePath && path.isAbsolute(candidatePath))
  const { candidate, assets } = loadCandidate(candidatePath)
  const frontendFiles = measuredFrontend(assets, candidate)
  const output = workspacePath(process.env.CI_NATIVE_OUTPUT || '.compatibility/native-safari')
  fs.mkdirSync(output, { recursive: true })
  const finalPath = workspacePath(path.join(output, 'native-smoke.json'))
  const provisionalPath = workspacePath(path.join(output, 'native-smoke.provisional.json'))
  assert(!fs.existsSync(finalPath) && !fs.existsSync(provisionalPath), 'Native receipt paths must be fresh')
  const network = createNetworkGuard()
  let provisional
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-native-'))
  const cert = path.join(temp, 'cert.pem'), key = path.join(temp, 'key.pem')
  const sockets = new Set()
  let server, driver, fingerprint, trusted = false
  let applicationFrames = 0, protocolErrors = 0, ticker
  const cleanupErrors = []
  try {
    // Test certificate only, installed solely in the disposable hosted runner.
    fixtureCertificate(temp)
    fingerprint = certificateFingerprint(cert)
    // Even a timed-out or partially successful trust installation must be cleaned up.
    trusted = true
    execFileSync('/usr/bin/sudo', ['/usr/bin/security', 'add-trusted-cert', '-d', '-r', 'trustRoot', '-k', '/Library/Keychains/System.keychain', cert], { stdio: 'inherit', timeout: 30000, env: { ...process.env, PATH: TRUSTED_PATH } })
    const state = { data_source: 'mqtt', native_connected: true, mqtt_connected: true, controller_controls_available: true, controller_observed_at: Date.now() / 1000, dry_run: false, battery_soc: 73, gt: 420, tt: 850, solar_total: 1200, loads: {}, features: { ha: false }, telemetry: { quality: 'live', source: 'mqtt', observed_at: new Date().toISOString() }, ui_config: { settings: { mqtt_port: 1883 } } }
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.webmanifest': 'application/manifest+json', '.json': 'application/json', '.woff2': 'font/woff2', '.png': 'image/png', '.ico': 'image/x-icon' }
    server = https.createServer({ cert: fs.readFileSync(cert), key: fs.readFileSync(key) }, network.wrap(() => `https://127.0.0.1:${server.address().port}`, (req, res) => {
      const route = new URL(req.url, 'https://localhost').pathname
      res.setHeader('Cache-Control', 'no-cache')
      res.setHeader('Content-Type', 'application/json')
      if (route === '/api/state') return res.end(JSON.stringify(state))
      if (route.startsWith('/api/notifications/')) return res.end(JSON.stringify({ enabled: false, available: false, items: [], notifications: [] }))
      const file = route === '/' ? '/index.html' : route
      if (!assets.has(file)) { res.writeHead(404); return res.end('{}') }
      res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream')
      res.end(assets.get(file))
    }))
    server.on('upgrade', (req, socket) => {
      if (new URL(req.url, 'https://localhost').pathname !== '/ws' || !req.headers['sec-websocket-key']) { socket.destroy(); return }
      let accept
      try { accept = websocketAccept(req.headers['sec-websocket-key']) } catch { socket.destroy(); return }
      socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`)
      sockets.add(socket); socket.once('close', () => sockets.delete(socket)); socket.on('error', () => sockets.delete(socket))
      const readFrame = clientFrameReader((opcode, payload) => {
        if ([0, 1, 2].includes(opcode)) applicationFrames++
        else if (opcode === 8) socket.end(Buffer.from([0x88, 0]))
        else if (opcode === 9 && payload.length <= 125) socket.write(Buffer.concat([Buffer.from([0x8a, payload.length]), payload]))
        else if (opcode !== 10) { protocolErrors++; socket.destroy() }
      })
      socket.on('data', frame => { try { readFrame(frame) } catch { protocolErrors++; socket.destroy() } })
      socket.write(websocketFrame(state))
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    ticker = setInterval(() => { for (const socket of sockets) socket.write(websocketFrame(state)) }, 1000)
    await run('/usr/bin/sudo', ['/usr/bin/safaridriver', '--enable'], 30000)
    const port = await unusedPort()
    driver = spawn('/usr/bin/safaridriver', ['-p', String(port)], { stdio: 'inherit', env: { ...process.env, PATH: TRUSTED_PATH } })
    driver.on('error', () => {})
    await waitForNativeDriver(driver, `http://127.0.0.1:${port}/status`)
    await run(python, ['-E', '-s', path.join(__dirname, 'native_acceptance_webdriver.py'), '--candidate', candidatePath, '--base-url', `https://127.0.0.1:${server.address().port}`, '--driver-url', `http://127.0.0.1:${port}`, '--platform', 'macOS', '--runtime', 'native-host', '--output', output])
    provisional = JSON.parse(fs.readFileSync(provisionalPath, 'utf8'))
  } finally {
    clearInterval(ticker)
    try { await terminate(driver) } catch (error) { cleanupErrors.push(error.message) }
    for (const socket of sockets) socket.destroy()
    try { if (server) await closeFixtureServer(server) } catch { cleanupErrors.push('Fixture HTTPS server cleanup failed') }
    if (trusted) cleanupErrors.push(...removeFixtureTrust(cert, fingerprint))
    try { fs.rmSync(temp, { recursive: true, force: true }) } catch { cleanupErrors.push('Temporary fixture directory cleanup failed') }
    if (cleanupErrors.length) { console.error(cleanupErrors.join('; ')); process.exitCode = 1 }

  }
  // No positive receipt exists until child cleanup, parent counters and all teardown checks pass.
  measuredFrontend(assets, candidate)
  const receipt = { ...finalizeNativeReceipt(provisional, network.observed, { applicationFrames, protocolErrors }, cleanupErrors), frontendFiles }
  fs.writeFileSync(finalPath, JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' })
}
module.exports = { websocketFrame, clientFrameReader, websocketAccept, certificateFingerprint, closeFixtureServer, removeFixtureTrust, waitForNativeDriver }
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1 })
