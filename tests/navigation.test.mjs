import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const app = readFileSync(new URL('../index.jsx', import.meta.url), 'utf8')
const picker = readFileSync(new URL('../ui/ModelPicker.jsx', import.meta.url), 'utf8')

test('report detail follows the reversible shell navigation outcome contract', () => {
  assert.match(app, /nav\.open\('reflection-report',\s*\{[\s\S]*onBack:[\s\S]*onForward:/)
  assert.match(app, /const \{ status \} = await handle\.outcome/)
  assert.match(app, /status !== 'owned' && status !== 'standalone'/)
  assert.doesNotMatch(app, /handle\.ready/)
})

test('the model sheet follows the same reversible outcome contract', () => {
  assert.match(picker, /nav\.open\(navKey,\s*\{[\s\S]*onBack:[\s\S]*onForward:/)
  assert.match(picker, /const \{ status \} = await handle\.outcome/)
  assert.match(picker, /status !== 'owned' && status !== 'standalone'/)
  assert.doesNotMatch(picker, /handle\.ready/)
})
