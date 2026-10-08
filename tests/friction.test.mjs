import assert from 'node:assert/strict'
import test from 'node:test'
import { backlogSections, frictionRows, isRunChat, reportHeadline } from '../friction.js'

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

test('proposal lifecycle events never become friction outcomes in the backlog', () => {
  const friction = line({ id: 'a', friction: 'example' })
  const outcomes = [
    line({ friction_id: 'a', outcome: 'asked', run: 'complete' }),
    line({ kind: 'proposal_event', proposal_id: 'a', friction_id: 'a', stage: 'verified' }),
  ].join('\n')
  const rows = frictionRows(friction, outcomes, [{ id: 'complete', report: '# Proposal' }])
  assert.equal(rows[0].outcome.outcome, 'asked')
  assert.deepEqual(backlogSections(rows, []).decision.map((row) => row.id), ['a'])
})
