import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const fetchSource = fs.readFileSync(new URL('../fetch.sh', import.meta.url), 'utf8')
const runnerSource = fs.readFileSync(
  new URL('../reflection_runner.py', import.meta.url),
  'utf8',
)

test('the platform supervisor is the only cron outcome writer', () => {
  assert.doesNotMatch(fetchSource, /emit_outcome/)
  assert.doesNotMatch(fetchSource, /api\/admin\/activity\/emit/)
})

test('Reflection never sweeps unattended changes into a broad safety-net commit', () => {
  assert.doesNotMatch(fetchSource, /pm-commit\s+--allow-broad/)
  assert.doesNotMatch(runnerSource, /pm-commit[\s\S]*--allow-broad/)
  assert.doesNotMatch(runnerSource, /_safety_snapshot/)
  assert.doesNotMatch(fetchSource, /nightly safety-net commit/)
})
