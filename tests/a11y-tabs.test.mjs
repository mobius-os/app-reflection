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

test('agent overrides default to Background agents and use the production-style dialog picker', () => {
  const settings = readFileSync(new URL('../ui/SettingsTab.jsx', import.meta.url), 'utf8')
  const picker = readFileSync(new URL('../ui/ModelPicker.jsx', import.meta.url), 'utf8')
  assert.match(settings, /useState\(true\)/)
  assert.match(settings, /primaryMode === 'custom'/)
  assert.match(settings, /secondaryMode === 'custom'/)
  assert.match(settings, /Background agents/)
  assert.match(settings, /<ModelPicker/)
  assert.doesNotMatch(settings, /<select[\s\S]*Reflection primary model/)
  assert.match(picker, /role="dialog"/)
  assert.match(picker, /aria-modal="true"/)
  assert.match(picker, /event\.key !== 'Tab'/)
  assert.match(picker, /triggerRef\.current\?\.focus/)
})
