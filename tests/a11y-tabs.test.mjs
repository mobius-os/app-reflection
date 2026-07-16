import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const app = readFileSync(new URL('../index.jsx', import.meta.url), 'utf8')

test('brief and settings tabs use roving focus and labelled tab panels', () => {
  assert.match(app, /tabIndex=\{tab === 'reports' \? 0 : -1\}/)
  assert.match(app, /event\.key === 'ArrowRight'/)
  assert.match(app, /event\.key === 'Home'/)
  assert.match(app, /role="tabpanel" aria-labelledby="rf-tab-reports"/)
  assert.match(app, /role="tabpanel" aria-labelledby="rf-tab-settings"/)
})
