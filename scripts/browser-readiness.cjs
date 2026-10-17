const assert = require('node:assert/strict')

/** Wait for this document's fixture telemetry, not for background traffic to stop. */
async function waitForHydratedDashboard(page, expectedSoc, publicMode = false) {
  assert(Number.isFinite(expectedSoc), 'Readiness requires the current fixture state of charge')
  await Promise.all([
    page.getByText(`${expectedSoc}%`, { exact: true }).first().waitFor({ state: 'visible' }),
    publicMode
      ? page.getByText('PUBLIC · READ-ONLY', { exact: true }).waitFor({ state: 'visible' })
      : page.locator('[data-testid="transport-status"][data-connected="true"]').waitFor({ state: 'visible' }),
  ])
}

module.exports = { waitForHydratedDashboard }
