import assert from 'node:assert/strict'
import test from 'node:test'
import { dailyCron, scheduleTime } from '../schedule.js'

test('a zone-owned schedule shows its own wall-clock time', () => {
  assert.equal(scheduleTime({ zone_cron: '30 7 * * *', timezone: 'Europe/London' }), '07:30')
})

test('a server-time schedule is shown in local time', () => {
  const now = new Date(Date.UTC(2026, 8, 26, 12))
  const local = new Date(Date.UTC(2026, 8, 26, 6, 0))
  const expected = `${String(local.getHours()).padStart(2, '0')}:${String(local.getMinutes()).padStart(2, '0')}`
  assert.equal(scheduleTime({ cron: '0 6 * * *' }, now), expected)
})

test('anything but a plain daily time is not shown as one', () => {
  assert.equal(scheduleTime({ cron: '*/15 * * * *' }), null)
  assert.equal(scheduleTime(null), null)
})

test('a chosen time becomes a daily cron', () => {
  assert.equal(dailyCron('07:30'), '30 7 * * *')
})
