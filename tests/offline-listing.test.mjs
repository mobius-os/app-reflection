import assert from 'node:assert/strict'
import test from 'node:test'

import { makeStorage } from '../storage-core.js'
import { reconcileReportDates } from '../report-list-state.js'

test('report discovery stays honest through online, offline, and reconnect listings', async (t) => {
  const previousWindow = globalThis.window
  t.after(() => { globalThis.window = previousWindow })

  let listing = {
    entries: [{ name: '2026-09-20.html', path: 'reports/2026-09-20.html', type: 'file' }],
    complete: true,
    source: 'server',
  }
  globalThis.window = {
    mobius: {
      online: true,
      storage: {
        list: async () => listing.entries,
        listWithStatus: async () => listing,
      },
    },
  }

  const storage = makeStorage('4', 'app-token')
  assert.deepEqual(await storage.listReportDates(), {
    dates: ['2026-09-20'],
    complete: true,
  })

  listing = {
    entries: [{ name: '2026-09-21.html', path: 'reports/2026-09-21.html', type: 'file' }],
    complete: false,
    source: 'derived',
  }
  globalThis.window.mobius.online = false
  assert.deepEqual(await storage.listReportDates(), {
    dates: ['2026-09-21'],
    complete: false,
  })

  listing = {
    entries: [
      { name: '2026-09-22.html', path: 'reports/2026-09-22.html', type: 'file' },
      { name: '2026-09-21.html', path: 'reports/2026-09-21.html', type: 'file' },
    ],
    complete: true,
    source: 'server',
  }
  globalThis.window.mobius.online = true
  assert.deepEqual(await storage.listReportDates(), {
    dates: ['2026-09-22', '2026-09-21'],
    complete: true,
  })
})

test('report rows preserve partial results and replace from complete snapshots', () => {
  assert.deepEqual(reconcileReportDates(['2026-09-22'], { dates: [], complete: false }), {
    dates: ['2026-09-22'], usable: true,
  })
  assert.deepEqual(reconcileReportDates(['2026-09-22'], {
    dates: ['2026-09-23'], complete: false,
  }), {
    dates: ['2026-09-23', '2026-09-22'], usable: true,
  })
  assert.deepEqual(reconcileReportDates(['2026-09-22'], { dates: [], complete: true }), {
    dates: [], usable: true,
  })
})

test('a standalone first-run 404 is a complete empty report directory', async (t) => {
  const previousWindow = globalThis.window
  const previousFetch = globalThis.fetch
  t.after(() => {
    globalThis.window = previousWindow
    globalThis.fetch = previousFetch
  })
  delete globalThis.window
  globalThis.fetch = async () => ({ status: 404, ok: false })
  assert.deepEqual(await makeStorage('4', 'app-token').listReportDates(), {
    dates: [], complete: true,
  })
})
