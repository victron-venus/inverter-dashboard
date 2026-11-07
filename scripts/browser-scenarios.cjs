/** Shared browser-visible behavior. All commands terminate at the loopback fixture. */
const assert = require('node:assert/strict')

const MODES = [
  ['off', 'Off'], ['on', 'On'],
  ['optimized_with_battery_life', 'Optimized with battery life'],
  ['optimized_without_battery_life', 'Optimized without battery life'],
  ['keep_batteries_charged', 'Keep batteries charged'], ['external_control', 'External control'],
]
// Expectations intentionally remain separate from the bundled translation catalogs.
const LOCALES = [
  ['en', 'Settings', 'Configuration', 'Close settings', 'Off', 'MQTT port', 'Save Configuration', 'MQTT port must be an integer from 1 to 65535.'],
  ['ru', 'Настройки', 'Конфигурация', 'Закрыть настройки', 'Выкл.', 'Порт MQTT', 'Сохранить конфигурацию', 'Порт MQTT должен быть целым числом от 1 до 65535.'],
  ['uk', 'Налаштування', 'Конфігурація', 'Закрити налаштування', 'Вимк.', 'Порт MQTT', 'Зберегти конфігурацію', 'Порт MQTT має бути цілим числом від 1 до 65535.'],
  ['de', 'Einstellungen', 'Konfiguration', 'Einstellungen schließen', 'Aus', 'MQTT-Port', 'Konfiguration speichern', 'Der MQTT-Port muss eine ganze Zahl von 1 bis 65535 sein.'],
  ['fr', 'Paramètres', 'Configuration', 'Fermer les paramètres', 'Arrêt', 'Port MQTT', 'Enregistrer la configuration', 'Le port MQTT doit être un entier de 1 à 65535.'],
  ['nl', 'Instellingen', 'Configuratie', 'Instellingen sluiten', 'Uit', 'MQTT-poort', 'Configuratie opslaan', 'De MQTT-poort moet een geheel getal van 1 t/m 65535 zijn.'],
]

async function runDashboardScenarios({ page, fixture, scenario, broadcast, eventually, createContext, origin, waitForHydratedDashboard }) {
  const trigger = () => page.locator('.ess-mode-trigger:visible')
  const menu = () => page.getByRole('menu', { name: 'ESS mode', exact: true })
  const choice = label => page.getByRole('menuitemradio', { name: label, exact: true })
  function fresh() {
    fixture.state.controller_observed_at = Date.now() / 1000
    fixture.state.ess_mode_observed_at = Date.now() / 1000
    fixture.state.ha_observed_at = Date.now() / 1000
  }
  function update(patch) { Object.assign(fixture.state, patch); broadcast() }
  async function openMenu() {
    if (await menu().count() === 0) await trigger().click()
    await menu().waitFor()
  }
  async function closeMenu() {
    if (await menu().count()) await page.keyboard.press('Escape')
    await menu().waitFor({ state: 'hidden' })
  }
  const alarm = { id: 'victron-platform-fixture-1', level: 'alarm', title: 'Fixture retained battery alarm', body: 'Fixture battery only', source: 'victron', ts: new Date(Date.now() - 75 * 60000).toISOString() }
  const banner = () => page.getByText(alarm.title, { exact: true }).filter({ visible: true }).first().locator('..')
  const eventTime = () => banner().locator('time')

  await scenario('retained-warning-displays-source-time-not-receipt-time', async () => {
    update({ notifications: [alarm] })
    await eventTime().waitFor()
    assert.equal(await eventTime().getAttribute('datetime'), alarm.ts)
    assert.match(await eventTime().textContent(), /^1h 15m ago$/)
    const expected = await page.evaluate(ts => new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'long' }).format(Date.parse(ts)), alarm.ts)
    assert.equal(await eventTime().getAttribute('title'), expected)
  })
  await scenario('incomplete-warning-time-is-never-invented', async () => {
    update({ notifications: [{ ...alarm, ts: undefined }] })
    await eventually(() => banner().textContent(), value => /Event time unavailable/.test(value), 'Incomplete source time must be explicit')
    assert.equal(await eventTime().count(), 0)
    update({ notifications: [alarm] })
    await eventTime().waitFor()
    assert.equal(await eventTime().getAttribute('datetime'), alarm.ts)
  })
  await scenario('reconnect-replay-keeps-event-time-and-history-unique', async () => {
    fixture.paused = true
    for (const socket of fixture.sockets) socket.close()
    fixture.sockets.clear()
    await eventually(() => page.getByTestId('transport-status').getAttribute('data-connected'), value => value === 'false', 'Transport should disconnect')
    fixture.paused = false
    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    await eventually(() => page.getByTestId('transport-status').getAttribute('data-connected'), value => value === 'true', 'Retained snapshot should reconnect')
    update({ notifications: [{ ...alarm }] })
    assert.equal(await eventTime().getAttribute('datetime'), alarm.ts)
    assert.match(await eventTime().textContent(), /^1h 15m ago$/)
    await page.getByRole('button', { name: 'Notifications', exact: true }).click()
    const row = page.locator('.notification-bell button.text-left').filter({ hasText: alarm.title })
    await row.waitFor()
    assert.equal(await row.count(), 1, 'Replayed source identity must not duplicate history')
    await page.getByRole('button', { name: 'Notifications', exact: true }).click()
    update({ notifications: [] })
    await eventTime().waitFor({ state: 'hidden' })
  })

  fresh()
  update({ ess_mode: { selected: 'external_control', selection_supported: true }, ess_mode_controls_available: true })
  await scenario('external-menu-all-six-modes-use-correlated-observation', async () => {
    await openMenu()
    assert.deepEqual(await menu().getByRole('menuitemradio').allTextContents().then(labels => labels.map(label => label.trim())), MODES.map(([, label]) => label))
    assert.equal(await choice('External control').getAttribute('aria-checked'), 'true')
    for (const [id, label] of MODES) {
      fresh(); broadcast()
      await choice(label).click()
      const command = await eventually(() => fixture.commands.at(-1), value => value?.action === 'set_ess_mode' && value.mode === id, `Mode ${id} must use its wire contract`)
      assert.match(command.request_id, /^[0-9a-f-]{36}$/)
      // A transport acceptance is insufficient: selection stays observed until telemetry confirms.
      assert.notEqual(await choice(label).getAttribute('aria-checked'), 'true')
      update({ ess_mode: { selected: id, selection_supported: true, request_id: command.request_id } })
      await eventually(() => choice(label).getAttribute('aria-checked'), value => value === 'true', 'Controller observation must confirm selection')
      await eventually(() => trigger().getAttribute('aria-busy'), value => value === null, 'Correlated observation must resolve pending state')
    }
    const before = fixture.commands.length
    await choice('External control').click()
    await menu().waitFor({ state: 'hidden' })
    assert.equal(fixture.commands.length, before, 'Selecting current mode must not send another command')
  })
  await scenario('external-menu-stale-and-dry-controls-send-nothing', async () => {
    const before = fixture.commands.length
    update({ ess_mode_observed_at: Date.now() / 1000 - 120 })
    await openMenu()
    await eventually(() => choice('Off').getAttribute('aria-disabled'), value => value === 'true', 'Stale state must disable mode choices')
    await choice('Off').focus(); await page.keyboard.press('Enter')
    fresh(); update({ dry_run: true })
    await page.getByText('Mode changes are unavailable in DRY mode.', { exact: true }).waitFor()
    await choice('Off').focus(); await page.keyboard.press('Enter')
    assert.equal(fixture.commands.length, before)
    update({ dry_run: false }); await closeMenu()
  })
  await scenario('external-rejection-and-unrelated-replies-do-not-change-selection', async () => {
    fixture.hold = true; fresh(); broadcast(); await openMenu()
    await choice('Off').click()
    const command = await eventually(() => fixture.commands.at(-1), value => value?.action === 'set_ess_mode' && value.mode === 'off', 'Mode command must be pending')
    for (const socket of fixture.sockets) socket.send(JSON.stringify({ type: 'command_error', action: command.action, request_id: 'old-connection-request', error: 'Obsolete rejection' }))
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    assert.equal(await trigger().getAttribute('aria-busy'), 'true', 'Unrelated delayed reply must not finish a newer command')
    assert.equal(await choice('External control').getAttribute('aria-checked'), 'true')
    for (const socket of fixture.sockets) socket.send(JSON.stringify({ type: 'command_error', action: command.action, request_id: command.request_id, error: 'Synthetic mode rejection' }))
    await page.getByRole('alert').filter({ hasText: 'Synthetic mode rejection' }).waitFor()
    assert.equal(await choice('External control').getAttribute('aria-checked'), 'true')
    fixture.hold = false; await closeMenu()
  })

  const home = () => page.getByRole('button', { name: 'Fixture privacy', exact: true })
  fresh()
  fixture.state.ui_config.home_buttons = [{ id: 'privacy_fixture', label: 'Fixture privacy', entity: 'switch.fixture_privacy' }, { id: 'disabled_fixture', label: 'Fixture excluded', entity: 'switch.fixture_excluded', enabled: false }]
  update({ features: { ha: true }, booleans: { home_privacy_fixture: false }, ha_direct_connected: true, ha_controls_available: true })
  await scenario('home-controls-use-only-configured-entities-and-fresh-state', async () => {
    await home().waitFor()
    await eventually(() => home().isEnabled(), Boolean, 'Configured fresh Home button must be enabled')
    assert.equal(await page.getByRole('button', { name: 'Fixture excluded', exact: true }).count(), 0)
    assert.equal(await home().getAttribute('aria-pressed'), 'false')
    await home().click()
    await eventually(() => fixture.commands.at(-1), value => value?.action === 'toggle' && value.entity === 'switch.fixture_privacy', 'Home command must target only configured fixture entity')
    assert.equal(await home().getAttribute('aria-pressed'), 'false', 'Transport acceptance must not invent observed Home state')
    update({ booleans: { home_privacy_fixture: true } })
    await eventually(() => home().getAttribute('aria-pressed'), value => value === 'true', 'Live Home observation must update pressed state')
    update({ ha_observed_at: Date.now() / 1000 - 120 })
    await eventually(() => home().isDisabled(), Boolean, 'Stale Home state must disable controls')
    fresh(); update({ booleans: {} })
    await eventually(() => home().isDisabled(), Boolean, 'Unknown Home state must disable controls')
    update({ booleans: { home_privacy_fixture: true } })
  })
  await scenario('home-command-rejection-does-not-optimistically-toggle', async () => {
    fixture.reject = true; fresh(); broadcast()
    await eventually(() => home().isEnabled(), Boolean, 'Fresh Home state must recover')
    await home().click()
    await page.getByText('The server rejected the command.', { exact: false }).waitFor()
    assert.equal(await home().getAttribute('aria-pressed'), 'true')
    fixture.reject = false
  })
  const pump = () => page.getByRole('button', { name: /^PUMP manual mode$/i })
  const valve = () => page.getByRole('button', { name: /^VALVE manual mode$/i })
  update({ water_level: 41, pump_switch: true, water_valve: false, water_pump_controls_available: true, water_valve_controls_available: false })
  await scenario('water-controls-have-independent-availability-and-confirmation', async () => {
    await pump().waitFor(); assert(await pump().isEnabled()); assert(await valve().isDisabled())
    assert.equal(await pump().getAttribute('aria-pressed'), 'true')
    assert.equal(await valve().getAttribute('aria-pressed'), 'false')
    await pump().click()
    await eventually(() => fixture.commands.at(-1), value => value?.action === 'water_mode' && value.which === 'pump' && value.mode === 2, 'Pump off wire contract')
    assert.equal(await pump().getAttribute('aria-pressed'), 'true')
    update({ water_valve_controls_available: true })
    await eventually(() => valve().isEnabled(), Boolean, 'Valve should be independently available')
    const before = fixture.commands.length
    page.once('dialog', dialog => dialog.dismiss())
    await valve().click()
    assert.equal(fixture.commands.length, before, 'Declined valve confirmation must send nothing')
    page.once('dialog', dialog => dialog.accept())
    await valve().click()
    await eventually(() => fixture.commands.at(-1), value => value?.action === 'water_mode' && value.which === 'valve' && value.mode === 1, 'Confirmed valve command wire contract')
    assert.equal(await valve().getAttribute('aria-pressed'), 'false')
  })
  await scenario('water-command-rejection-preserves-observed-state', async () => {
    fixture.reject = true
    await eventually(() => pump().isEnabled(), Boolean, 'Previous command must finish before rejection test')
    await pump().click()
    await page.getByText('The server rejected the command.', { exact: false }).waitFor()
    assert.equal(await pump().getAttribute('aria-pressed'), 'true')
    fixture.reject = false
  })
  await scenario('appliance-cards-require-running-and-enabled-state', async () => {
    for (const name of ['dishwasher', 'washer', 'dryer']) assert.equal(await page.getByTestId(name).count(), 0)
    update({ dishwasher_running: false, washer_time: 0, dryer_power: false })
    for (const name of ['dishwasher', 'washer', 'dryer']) assert.equal(await page.getByTestId(name).count(), 0)
    update({ dishwasher_running: true, washer_time: 120, dryer_power: true })
    for (const name of ['dishwasher', 'washer', 'dryer']) await page.getByTestId(name).waitFor()
    Object.assign(fixture.state.ui_config.settings, { show_dishwasher: false, show_washer: false, show_dryer: false }); broadcast()
    for (const name of ['dishwasher', 'washer', 'dryer']) await page.getByTestId(name).waitFor({ state: 'hidden' })
    Object.assign(fixture.state.ui_config.settings, { show_dishwasher: true, show_washer: true, show_dryer: true }); broadcast()
    for (const name of ['dishwasher', 'washer', 'dryer']) await page.getByTestId(name).waitFor()
    update({ dishwasher_running: false, washer_time: 0, dryer_power: false })
  })
  await scenario('six-locales-persist-without-server-configuration-writes', async () => {
    const before = fixture.commands.length
    let settingsLabel = 'Settings'
    for (const [locale, settings, title, close, off, port, save, error] of LOCALES) {
      await page.getByRole('button', { name: settingsLabel, exact: true }).click()
      await page.getByTestId('locale-select').selectOption(locale)
      await page.getByRole('dialog', { name: title, exact: true }).waitFor()
      assert.equal(await page.locator('html').getAttribute('lang'), locale)
      await page.getByLabel(port, { exact: true }).fill('70000')
      await page.getByRole('button', { name: save, exact: true }).click()
      await page.getByRole('alert').filter({ hasText: error }).waitFor()
      await page.getByRole('button', { name: close, exact: true }).click()
      await page.reload({ waitUntil: 'load' })
      await waitForHydratedDashboard(page, fixture.state.battery_soc)
      assert.equal(await page.locator('html').getAttribute('lang'), locale)
      await page.getByRole('button', { name: settings, exact: true }).waitFor()
      await trigger().click()
      await page.getByRole('menuitemradio', { name: off, exact: true }).waitFor()
      await page.keyboard.press('Escape')
      settingsLabel = settings
    }
    await page.getByRole('button', { name: settingsLabel, exact: true }).click()
    await page.getByTestId('locale-select').selectOption('en')
    await page.getByRole('button', { name: 'Close settings', exact: true }).click()
    assert.equal(fixture.commands.length, before, 'Locale is device-local and must not write server configuration')
  })
  await scenario('warning-exact-time-respects-timezone-and-dst', async () => {
    // The autumn fold repeats 01:30 in Los Angeles: two distinct instants must retain distinct offsets.
    const times = ['2026-11-01T08:30:00Z', '2026-11-01T09:30:00Z']
    update({ notifications: times.map((ts, index) => ({ ...alarm, id: `timezone-${index}`, title: `Fixture timezone ${index}`, ts })) })
    for (const timezoneId of ['America/Los_Angeles', 'Europe/Kyiv']) {
      const isolated = await createContext({ locale: 'en-US', timezoneId })
      try {
        const tab = await isolated.newPage(); await tab.goto(origin, { waitUntil: 'load' }); await waitForHydratedDashboard(tab, fixture.state.battery_soc)
        const titles = []
        for (const ts of times) {
          const time = tab.locator(`time[datetime="${ts}"]`); await time.waitFor()
          const expected = await tab.evaluate(value => new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'long' }).format(Date.parse(value)), ts)
          const title = await time.getAttribute('title'); assert.equal(title, expected); titles.push(title)
        }
        assert.notEqual(titles[0], titles[1], 'Distinct source instants must remain distinguishable across a DST fold')
        assert.equal(await tab.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone), timezoneId === 'Europe/Kyiv' ? await tab.evaluate(() => new Intl.DateTimeFormat('en', { timeZone: 'Europe/Kyiv' }).resolvedOptions().timeZone) : timezoneId)
      } finally { await isolated.close() }
    }
    update({ notifications: [] })
  })
  await scenario('touch-context-opens-menu-and-settings-without-overflow', async () => {
    fresh(); broadcast()
    const isolated = await createContext({ viewport: { width: 390, height: 844 }, hasTouch: true, locale: 'en-US' })
    try {
      const tab = await isolated.newPage(); await tab.goto(origin, { waitUntil: 'load' }); await waitForHydratedDashboard(tab, fixture.state.battery_soc)
      await tab.evaluate(() => {
        // Desktop WebKit can expose maxTouchPoints=0 with hasTouch enabled.
        // Observe genuine browser input instead of pretending to emulate hardware.
        window.__acceptanceTouchEvents = []
        document.addEventListener('touchstart', event => {
          window.__acceptanceTouchEvents.push({ trusted: event.isTrusted, touches: event.touches.length })
        })
      })
      await tab.locator('.ess-mode-trigger:visible').tap()
      assert(await tab.evaluate(() => window.__acceptanceTouchEvents.some(event => event.trusted && event.touches > 0)), 'A real browser-generated touch event must reach the page')
      const options = tab.getByRole('menuitemradio'); assert.equal(await options.count(), 6)
      assert(await tab.getByRole('menu', { name: 'ESS mode', exact: true }).isVisible())
      await tab.getByRole('menuitemradio', { name: 'External control', exact: true }).tap()
      await tab.getByRole('button', { name: 'Settings', exact: true }).tap()
      await tab.getByRole('dialog', { name: 'Configuration' }).waitFor()
      await tab.getByRole('button', { name: 'Close settings' }).tap()
      assert(await tab.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Touch layout must fit viewport')
    } finally { await isolated.close() }
  })
}
module.exports = { runDashboardScenarios, MODES, LOCALES }
