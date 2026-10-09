import assert from 'node:assert/strict'
import test from 'node:test'
import { backlogSections, createOlderReports, frictionRows, isRunChat, reportHeadline } from '../friction.js'

const line = (value) => JSON.stringify(value)

test('friction is listed newest first with its outcome, if any', () => {
  const friction = [
    line({ id: 'a', at: '2026-09-25T10:00:00Z', friction: 'first' }),
    line({ id: 'b', at: '2026-09-26T09:00:00Z', friction: 'second' }),
  ].join('\n')
  const outcomes = line({ friction_id: 'a', outcome: 'explained', note: 'One-off.' })

  const rows = frictionRows(friction, outcomes)

  assert.deepEqual(rows.map((row) => [row.id, row.outcome?.outcome ?? null]), [
    ['b', null], ['a', 'explained'],
  ])
})

test('missing files and half-written lines are simply skipped', () => {
  assert.deepEqual(frictionRows(undefined, null), [])
  const rows = frictionRows(`${line({ id: 'a', at: 'x', friction: 'ok' })}\n{"id":"b","fri`, '')
  assert.deepEqual(rows.map((row) => row.id), ['a'])
})

test('only chats the daily job opened are runs', () => {
  assert.equal(isRunChat({ scope: 'run-20260926T065626' }), true)
  assert.equal(isRunChat({ scope: null }), false)
  assert.equal(isRunChat({ scope: 'panel' }), false)
})

test('a report is headed by its first heading', () => {
  assert.equal(reportHeadline('# Two fixes worth making\n\nBody'), 'Two fixes worth making')
  assert.equal(reportHeadline('no heading'), '')
  assert.equal(reportHeadline(null), '')
})

test('the backlog separates waiting work, reported proposals, and what is done', () => {
  const rows = [
    { id: 'a', outcome: null },
    { id: 'b', outcome: { outcome: 'asked', run: 'r2' } },
    { id: 'c', outcome: { outcome: 'asked', run: 'r1' } },
    { id: 'd', outcome: { outcome: 'explained', run: 'r1' } },
  ]
  const runs = [
    { id: 'r1', awaiting_owner: false, report: '# done' },
    { id: 'r2', awaiting_owner: true, report: '# asks' },
  ]
  const sections = backlogSections(rows, runs)
  assert.deepEqual(
    [sections.pending, sections.decision, sections.done].map((list) => list.map((row) => row.id)),
    [['a'], ['b', 'c'], ['d']],
  )
  assert.equal(sections.working, false)
  assert.equal(backlogSections(rows, [...runs, { id: 'r3', running: true }]).working, true)
})

test('a reportless outcome stays visible as waiting after an interrupted run', () => {
  const friction = line({ id: 'a', friction: 'example' })
  const outcomes = line({ friction_id: 'a', outcome: 'asked', run: 'lost-report' })
  const visible = frictionRows(friction, outcomes, [{ id: 'lost-report', report: null }])
  assert.equal(visible[0].outcome, null)
  assert.deepEqual(backlogSections(visible, []).pending.map((row) => row.id), ['a'])
  assert.equal(frictionRows(friction, outcomes, [{ id: 'lost-report', report: '# Proposal' }])[0].outcome.outcome, 'asked')
})

test('a later orphan outcome does not hide an earlier reported outcome', () => {
  const friction = line({ id: 'a', friction: 'example' })
  const outcomes = [
    line({ friction_id: 'a', outcome: 'explained', run: 'complete' }),
    line({ friction_id: 'a', outcome: 'asked', run: 'lost-report' }),
  ].join('\n')
  assert.equal(frictionRows(friction, outcomes, [{ id: 'complete', report: '# Done' }])[0].outcome.outcome, 'explained')
})

test('an outcome stays joined to an older run whose report is not loaded yet', () => {
  const friction = line({ id: 'a', at: '2026-09-25T10:00:00Z', friction: 'first' })
  const outcomes = [
    line({ friction_id: 'a', outcome: 'asked', run: 'old' }),
  ].join('\n')
  const unloaded = [{ id: 'old', report: undefined, hasReport: true }]
  assert.equal(frictionRows(friction, outcomes, unloaded)[0].outcome?.run, 'old')
  const missing = [{ id: 'old', report: null, hasReport: false }]
  assert.equal(frictionRows(friction, outcomes, missing)[0].outcome, null)
})

test('a failed older-report read keeps offering a retry after a refresh', async () => {
  const older = createOlderReports()
  assert.equal(older.fields('run-1', true).report, undefined)
  const failed = await older.read('run-1', async () => { throw new Error('offline') })
  assert.deepEqual(failed, { report: undefined, hasReport: true, reportError: true })
  // A refresh rebuilds the run from what was remembered.
  assert.equal(older.fields('run-1', true).reportError, true)
  assert.equal(older.fields('run-1', null).reportError, true)

  const loaded = await older.read('run-1', async () => '# Report')
  assert.deepEqual(loaded, { report: '# Report', hasReport: true, reportError: false })
  assert.deepEqual(older.fields('run-1', true), loaded)
})

test('a remembered "no report" gives way once the listing has the report', async () => {
  const older = createOlderReports()
  await older.read('run-2', async () => null)
  assert.deepEqual(older.fields('run-2', null), { report: null, hasReport: false, reportError: false })
  assert.deepEqual(older.fields('run-2', false), { report: null, hasReport: false, reportError: false })
  // The report appears later: the run is shown as having one, and is read again when opened.
  assert.deepEqual(older.fields('run-2', true), { report: undefined, hasReport: true, reportError: false })
})

test('a negative listing preserves known absence through an outage until a report appears', async () => {
  const older = createOlderReports()
  const friction = line({ id: 'a', friction: 'example' })
  const outcomes = line({ friction_id: 'a', outcome: 'asked', run: 'old' })
  const absent = { report: null, hasReport: false, reportError: false }
  assert.deepEqual(await older.read('old', async () => null), absent)

  // Refresh first confirms absence, then cannot list reports at all.
  for (const listed of [false, null]) {
    const run = { id: 'old', ...older.fields('old', listed) }
    assert.deepEqual(run, { id: 'old', ...absent })
    const rows = frictionRows(friction, outcomes, [run])
    assert.equal(rows[0].outcome, null)
    assert.deepEqual(backlogSections(rows, [run]).pending.map((row) => row.id), ['a'])
  }

  // A positive listing restores the outcome without eagerly reading the text.
  const revived = { id: 'old', ...older.fields('old', true) }
  assert.deepEqual(revived, { id: 'old', report: undefined, hasReport: true, reportError: false })
  assert.equal(frictionRows(friction, outcomes, [revived])[0].outcome.outcome, 'asked')
  const loaded = await older.read('old', async () => '# Report')
  assert.deepEqual(loaded, { report: '# Report', hasReport: true, reportError: false })
  assert.deepEqual(older.fields('old', null), loaded)
})

test('an unread older run is assumed to have a report until the listing says otherwise', () => {
  const older = createOlderReports()
  assert.equal(older.fields('run-3', null).hasReport, true)
  assert.equal(older.fields('run-3', false).hasReport, false)
})

test('a positive listing supersedes absence even when the on-demand read fails', async () => {
  const older = createOlderReports()
  const friction = line({ id: 'a', friction: 'example' })
  const outcomes = line({ friction_id: 'a', outcome: 'asked', run: 'old' })
  assert.equal(older.fields('old', false).hasReport, false)
  assert.equal(older.fields('old', true).hasReport, true)

  const failed = await older.read('old', async () => { throw new Error('offline') })
  assert.deepEqual(failed, { report: undefined, hasReport: true, reportError: true })
  assert.deepEqual(older.fields('old', null), failed)
  const runs = [{ id: 'old', ...failed }]
  const rows = frictionRows(friction, outcomes, runs)
  assert.equal(rows[0].outcome.outcome, 'asked')
  assert.deepEqual(backlogSections(rows, runs).pending, [])

  const loaded = await older.read('old', async () => '# Report')
  assert.deepEqual(loaded, { report: '# Report', hasReport: true, reportError: false })
  assert.deepEqual(older.fields('old', true), loaded)
  assert.deepEqual(older.fields('old', null), loaded)
})

test('a positive listing supersedes absence across a later listing outage without a read', () => {
  const older = createOlderReports()
  const friction = line({ id: 'a', friction: 'example' })
  const outcomes = line({ friction_id: 'a', outcome: 'asked', run: 'old' })
  assert.equal(older.fields('old', false).hasReport, false)
  assert.equal(older.fields('old', true).hasReport, true)

  const present = older.fields('old', null)
  assert.deepEqual(present, { report: undefined, hasReport: true, reportError: false })
  assert.equal(frictionRows(friction, outcomes, [{ id: 'old', ...present }])[0].outcome.outcome, 'asked')

  // Another definitive absence still supersedes that positive listing.
  assert.equal(older.fields('old', false).hasReport, false)
  assert.equal(older.fields('old', null).hasReport, false)
})
