/** Final real-fixture command journal checks shared by runtime QA and controls. */
const assert = require('node:assert/strict')

async function verifyFinalCommandJournal(readFixture, expectedCaptures) {
  const final = await readFixture()
  assert.deepEqual(final.capture, expectedCaptures,
    'Unexpected late, duplicate or unconfigured command in the final fixture journal')
}

module.exports = { verifyFinalCommandJournal }
