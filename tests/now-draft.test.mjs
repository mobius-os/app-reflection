import assert from 'node:assert/strict'
import test from 'node:test'
import { buildNowDraft } from '../domain.js'

// buildNowDraft is the message "Do it now" auto-sends into a fresh chat. It
// must be self-contained: the receiving agent may know nothing about the
// brief, so every decision line needs the question, the chosen label(s), and
// the chosen option's description (where the brief agent packed the context).

const QUESTIONS = [
  {
    question: 'Ship the streak view?',
    header: 'Habits',
    multiSelect: false,
    options: [
      { label: 'Yes', description: 'Build it as proposed' },
      { label: 'No' },
    ],
  },
  {
    question: 'Which cleanups should I take?',
    header: 'Cleanup',
    multiSelect: true,
    options: [
      { label: 'Prune notes', description: 'Remove the 12 stale ones' },
      { label: 'Archive digests' },
    ],
  },
]

test('buildNowDraft carries question, labels, and option descriptions', () => {
  const draft = buildNowDraft(
    QUESTIONS,
    { 0: 'Yes', 1: ['Prune notes', 'Archive digests'] },
    '2026-08-05',
  )

  assert.match(draft, /Reflection brief \(2026-08-05\)/)
  assert.match(draft, /- Ship the streak view\?\n  → Yes \(Build it as proposed\)/)
  // Multi-select keeps every chosen label; a label with no description stays bare.
  assert.match(draft, /- Which cleanups should I take\?\n  → Prune notes \(Remove the 12 stale ones\); Archive digests/)
  // The framing must say the record is already saved, so the night run's
  // settled-batch contract holds from the agent's very first read.
  assert.match(draft, /already saved for tonight/)
})

test('buildNowDraft tolerates missing picks and unknown labels', () => {
  const draft = buildNowDraft(
    QUESTIONS,
    { 0: 'Maybe', 1: [] },
    '2026-08-05',
  )

  // An unknown label (no matching option) still lists verbatim, undecorated.
  assert.match(draft, /→ Maybe/)
  // An empty multi-select renders an empty decision line rather than throwing.
  assert.match(draft, /- Which cleanups should I take\?\n  → /)
})
