import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const fetchSource = fs.readFileSync(new URL('../fetch.sh', import.meta.url), 'utf8')
const statusSource = fs.readFileSync(
  new URL('../ui/LastNightStatus.jsx', import.meta.url),
  'utf8',
)

test('the platform supervisor is the only cron outcome writer', () => {
  assert.doesNotMatch(fetchSource, /emit_outcome/)
  assert.doesNotMatch(fetchSource, /api\/admin\/activity\/emit/)
})

test('the wrapper never sweeps unattended changes into a broad safety-net commit', () => {
  assert.doesNotMatch(fetchSource, /pm-commit\s+--allow-broad/)
  assert.doesNotMatch(fetchSource, /nightly safety-net commit/)
})

test('last-night status keys outcomes by installed app identity', () => {
  assert.match(statusSource, /&app_id=\$\{encodeURIComponent\(appId\)\}/)
  assert.match(statusSource, /Number\(e\.app_id\) === Number\(appId\)/)
  assert.doesNotMatch(statusSource, /e\.job === ['"]reflection['"]/)
})
