import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const detail = readFileSync(new URL('../ui/ReportDetail.jsx', import.meta.url), 'utf8')
const theme = readFileSync(new URL('../theme.js', import.meta.url), 'utf8')

test('report questions stay hidden until the iframe has reported its real height', () => {
  assert.match(detail, /const \[briefMeasured, setBriefMeasured\] = useState\(false\)/)
  assert.match(detail, /setBriefHeight\(Math\.min\(Math\.max\(h, 200\), 16000\)\)[\s\S]*setBriefMeasured\(true\)/)
  assert.match(detail, /\{briefMeasured && questions\.length > 0 && \(/)
})

test('the first report layout is measured invisibly behind the loading surface', () => {
  assert.match(detail, /state\.phase === 'ready' && !briefMeasured/)
  assert.match(detail, /rf-brief-panel\$\{briefMeasured \? '' : ' is-measuring'\}/)
  assert.match(theme, /\.rf-brief-panel\.is-measuring\s*\{[\s\S]*opacity:\s*0/)
  assert.doesNotMatch(theme, /\.rf-brief-panel\.is-measuring\s*\{[^}]*visibility:\s*hidden/)
})
