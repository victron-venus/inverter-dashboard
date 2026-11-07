/** Candidate-document isolation only; this does not configure host networking. */
const assert = require('node:assert/strict')
const crypto = require('node:crypto')

const PROBE_ORIGIN = 'https://127.0.0.1:65534'
const REPORT_PATH = '/__native/csp-report'
const STATUS_PATH = '/__native/csp-status'

function fixturePolicy(origin) {
  const parsed = new URL(origin)
  assert(parsed.protocol === 'https:' && parsed.hostname === '127.0.0.1' && parsed.port && parsed.pathname === '/' && !parsed.search && !parsed.hash, 'CSP requires an exact loopback fixture origin')
  assert.notEqual(parsed.origin, PROBE_ORIGIN, 'CSP fixture and control origins must differ')
  return [
    "default-src 'none'", "script-src 'self'", "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:", "font-src 'self' data:", "manifest-src 'self'",
    `connect-src 'self' wss://127.0.0.1:${parsed.port}`, "worker-src 'self'",
    "base-uri 'none'", "form-action 'none'", "frame-src 'none'", "object-src 'none'",
    "frame-ancestors 'none'", `report-uri ${REPORT_PATH}`,
  ].join('; ')
}

function createNetworkGuard() {
  let activePolicy, fixtureOrigin
  const observed = { probeReports: 0, candidateViolations: 0, malformedReports: 0, httpWrites: 0, policySha256: null }
  function acceptReport(document) {
    const report = document?.['csp-report']
    assert(report && typeof report['blocked-uri'] === 'string' && typeof report['effective-directive'] === 'string', 'Malformed CSP report')
    assert(report['original-policy'] === activePolicy && typeof report['document-uri'] === 'string' && new URL(report['document-uri']).origin === fixtureOrigin, 'CSP report is not bound to the fixture policy and origin')
    let blockedOrigin
    try { blockedOrigin = new URL(report['blocked-uri']).origin } catch { blockedOrigin = report['blocked-uri'] }
    // WebKit omits disposition in legacy reports; the driver separately requires its enforced DOM event.
    const enforcing = report.disposition === 'enforce' || report.disposition === undefined
    if (blockedOrigin === PROBE_ORIGIN && report['effective-directive'] === 'connect-src' && enforcing) observed.probeReports++
    else observed.candidateViolations++
  }
  function reportRequest(req, res) {
    if (req.method !== 'POST') { observed.malformedReports++; res.writeHead(405); res.end('{}'); return }
    let bytes = 0, body = ''
    req.on('data', chunk => {
      bytes += chunk.length
      if (bytes > 16384) { observed.malformedReports++; req.destroy(); return }
      body += chunk
    })
    req.once('end', () => {
      try { acceptReport(JSON.parse(body)); res.writeHead(204); res.end() }
      catch { observed.malformedReports++; res.writeHead(400); res.end('{}') }
    })
    req.once('error', () => { observed.malformedReports++ })
  }
  function wrap(origin, handler) {
    return (req, res) => {
      fixtureOrigin = origin()
      const policy = fixturePolicy(fixtureOrigin)
      activePolicy = policy
      observed.policySha256 = crypto.createHash('sha256').update(policy).digest('hex')
      res.setHeader('Content-Security-Policy', policy)
      res.setHeader('Referrer-Policy', 'no-referrer')
      res.setHeader('X-DNS-Prefetch-Control', 'off')
      res.setHeader('X-Content-Type-Options', 'nosniff')
      const route = new URL(req.url, origin()).pathname
      if (route === REPORT_PATH) { reportRequest(req, res); return }
      if (!['GET', 'HEAD'].includes(req.method)) {
        observed.httpWrites++; res.writeHead(405); res.end('{}'); return
      }
      if (route === STATUS_PATH) {
        res.setHeader('Content-Type', 'application/json'); res.setHeader('Cache-Control', 'no-store')
        res.end(JSON.stringify(observed)); return
      }
      handler(req, res)
    }
  }
  return { observed, wrap, acceptReport }
}

function finalizeNativeReceipt(provisional, network, counters, cleanupErrors) {
  assert(provisional?.scope === 'native-smoke' && provisional.status === 'pending-parent-validation', 'Native child observation is not provisional success')
  assert.equal(provisional.sessionCleanupSucceeded, true, 'Native session cleanup did not succeed')
  assert.notEqual(provisional.sessionCleanupFailed, true, 'Native session cleanup failed')
  assert.equal(provisional.cspProbeObserved, true, 'Native CSP enforcement was not observed')
  assert.deepEqual(cleanupErrors, [], 'Native parent cleanup failed')
  assert.equal(counters.applicationFrames, 0, 'Observed startup/settings flow sent application messages')
  assert.equal(counters.protocolErrors, 0, 'Malformed browser WebSocket traffic')
  assert.equal(network.httpWrites, 0, 'Native smoke must not issue HTTP settings or control writes')
  assert.equal(network.probeReports, 1, 'Expected exactly one enforced CSP control report')
  assert.equal(network.candidateViolations, 0, 'Candidate attempted a forbidden resource')
  assert.equal(network.malformedReports, 0, 'Malformed CSP report')
  assert.match(network.policySha256, /^[a-f0-9]{64}$/)
  return {
    ...provisional, status: 'passed', finishedAt: new Date().toISOString(),
    parentValidation: { complete: true, applicationFrames: 0, protocolErrors: 0, httpWrites: 0, cleanupSucceeded: true },
    networkIsolation: { mechanism: 'content-security-policy', scope: 'candidate-document-resources', enforced: true, probeObserved: true, probeReports: 1, candidateViolations: 0, malformedReports: 0, policySha256: network.policySha256 },
  }
}

module.exports = { fixturePolicy, createNetworkGuard, finalizeNativeReceipt, PROBE_ORIGIN, REPORT_PATH, STATUS_PATH }
