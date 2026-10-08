import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { frictionRows, isRunChat } from '../friction.js'

// Execute the actual source hook (no rewritten cache logic, JSX compiler,
// installed React, network, or provider). Effects/deps model a mounted view.
const source = readFileSync(new URL('../index.jsx', import.meta.url), 'utf8')
const helpers = source.slice(source.indexOf('function api('), source.indexOf('// Opens a chat'))
const hookSource = source.slice(source.indexOf('async function readTextOrThrow('), source.indexOf('function statusLine('))
const reportEffect = source.slice(source.indexOf('  const pending = run.report === undefined'), source.indexOf('  const html = run.report ?'))
function harness({ names = [], text = null, read = null } = {}) {
  const slots = [], effects = []
  let cursor = 0, value, open = false, listing = names, listingOk = true, body = text, request = read
  let reads = 0
  const listeners = new Map(), timers = new Set()
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]))
  const hooks = {
    useState(initial) {
      const i = cursor++
      slots[i] ??= { value: initial }
      return [slots[i].value, (next) => { slots[i].value = typeof next === 'function' ? next(slots[i].value) : next; render() }]
    },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial } },
    useCallback(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) slots[i] = { fn, deps }; return slots[i].fn },
    useEffect(fn, deps) {
      const i = cursor++
      if (!same(slots[i]?.deps, deps)) {
        const previous = slots[i]
        slots[i] = { deps }
        effects.push(() => { previous?.cleanup?.(); slots[i].cleanup = fn() })
      }
    },
  }
  const chats = Array.from({ length: 11 }, (_, i) => ({ id: `r${i}`, scope: 'run-test', created_at: String(99 - i), running: i === 10 }))
  const response = (status, text = '', json = {}) => ({ status, ok: status === 200, text: async () => text, json: async () => json })
  const context = vm.createContext({ ...hooks, frictionRows, isRunChat,
    window: { mobius: { storage: { getText: async () => null } } },
    document: { visibilityState: 'visible', addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name) => listeners.delete(name) },
    setInterval: (fn) => { timers.add(fn); return fn }, clearInterval: (fn) => timers.delete(fn),
    fetch: async (path) => {
      if (path === '/api/app-chats') return response(200, '', chats)
      if (path === '/api/apps/schedules') return response(200, '', [])
      if (path.includes('/apps-list/')) return response(listingOk ? 200 : 503, '', { entries: listing.map((name) => ({ name, type: 'file' })) })
      if (path.endsWith('/friction.jsonl')) return response(200, JSON.stringify({ id: 'f', friction: 'mock' }))
      if (path.endsWith('/outcomes.jsonl')) return response(200, JSON.stringify({ friction_id: 'f', run: 'r10', outcome: 'asked' }))
      if (path.endsWith('/reports/r10.md')) {
        reads++
        if (request) return await request()
        return response(body === null ? 404 : 200, body)
      }
      if (/\/reports\/r[0-9]+\.md$/.test(path)) return response(404)
      throw new Error(`Unexpected mock request ${path}`) // no fallback I/O
    },
  })
  vm.runInContext(`${helpers}\n${hookSource}\nthis.useData = useData; this.reportView = (run, loadReport) => { ${reportEffect} };`, context)
  function render() {
    cursor = 0
    value = context.useData('fixture', 'synthetic')
    const run = value[0]?.runs.find((r) => r.id === 'r10')
    if (open && run) context.reportView(run, value[3])
    while (effects.length) effects.shift()()
  }
  const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve() }
  render()
  return {
    flush, run: () => value[0]?.runs.find((r) => r.id === 'r10'), rows: () => value[0]?.rows,
    reads: () => reads, open: async () => { open = true; render(); await flush() },
    listing: (names) => { listing = names }, available: (ok) => { listingOk = ok }, body: (text) => { body = text },
    request: (fn) => { request = fn }, retry: () => value[3]('r10'),
    visible: async () => { listeners.get('visibilitychange')(); await flush() },
    poll: async () => { for (const fn of timers) fn(); await flush() },
  }
}

test('an older cached missing report becomes present on authoritative visibility reload', async () => {
  const h = harness(); await h.flush(); await h.open()
  assert.equal(h.run().report, null)
  h.listing(['r10.md']); h.body('# Published')
  await h.visible()
  assert.equal(h.run().report, '# Published')
  assert.equal(h.run().hasReport, true)
  assert.equal(h.rows()[0].outcome?.run, 'r10')
})

test('an older cached report becomes absent on authoritative poll but survives an unavailable listing', async () => {
  const h = harness({ names: ['r10.md'], text: '# Saved' }); await h.flush(); await h.open()
  const reads = h.reads()
  await h.visible()
  h.available(false); await h.visible()
  assert.equal(h.reads(), reads, 'valid cached text avoids repeated reads')
  assert.equal(h.run().report, '# Saved')
  h.available(true); h.listing([]); h.body(null); await h.poll()
  assert.equal(h.run().report, null)
  assert.equal(h.run().hasReport, false)
  assert.equal(h.rows()[0].outcome, null)
})

for (const event of ['visible', 'poll']) test(`failed older read keeps retry after ${event} reload`, async () => {
  const h = harness({ names: ['r10.md'], read: async () => { throw new Error('mock offline') } })
  await h.flush(); await h.open()
  assert.equal(h.run().reportError, true)
  const reads = h.reads()
  await h[event]()
  assert.equal(h.run().reportError, true, 'retry must not turn into idle Loading')
  assert.equal(h.reads(), reads, 'refresh does not secretly start a duplicate read')
  h.request(null); h.body('# Recovered'); await h.retry()
  assert.equal(h.run().report, '# Recovered')
  assert.equal(h.run().reportError, false)
})

test('a pending older read survives refresh and cannot resurrect a freshly deleted report', async () => {
  let resolve
  const h = harness({ names: ['r10.md'], read: () => new Promise((done) => { resolve = done }) })
  await h.flush(); await h.open()
  const reads = h.reads()
  await h.visible(); await h.poll()
  assert.equal(h.reads(), reads)
  h.listing([]); await h.visible()
  resolve({ ok: true, status: 200, text: async () => '# Obsolete' }); await h.flush()
  assert.equal(h.run().report, null)
  assert.equal(h.run().hasReport, false)
})

test('a pending older read completes after visibility and poll without duplicate requests', async () => {
  let resolve
  const h = harness({ names: ['r10.md'], read: () => new Promise((done) => { resolve = done }) })
  await h.flush(); await h.open()
  const reads = h.reads()
  await h.visible(); await h.poll(); await h.retry()
  assert.equal(h.run().report, undefined)
  assert.equal(h.run().reportError, false)
  assert.equal(h.reads(), reads)
  resolve({ ok: true, status: 200, text: async () => '# Completed' }); await h.flush()
  assert.equal(h.run().report, '# Completed')
  assert.equal(h.run().hasReport, true)
})
