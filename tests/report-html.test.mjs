import assert from 'node:assert/strict'
import { mkdir, rm } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import test from 'node:test'

const execFileAsync = promisify(execFile)
const root = dirname(fileURLToPath(import.meta.url))
const buildDir = join(root, '.build')
const bundled = join(buildDir, 'index.mjs')

async function bundle() {
  await rm(buildDir, { recursive: true, force: true })
  await mkdir(buildDir, { recursive: true })
  await execFileAsync('/home/hmzmrzx/projects/mobius/frontend/node_modules/.bin/esbuild', [
    join(root, '..', 'index.jsx'),
    '--bundle',
    '--format=esm',
    '--platform=node',
    '--jsx=automatic',
    `--outfile=${bundled}`,
  ], { env: { ...process.env, NODE_PATH: '/home/hmzmrzx/projects/mobius/frontend/node_modules' } })
  return import(pathToFileURL(bundled))
}

test('hardenReportHtml injects a restrictive CSP into full reports', async () => {
  const { hardenReportHtml } = await bundle()

  const html = '<!doctype html><html><head><title>Brief</title></head><body><h1>Morning</h1></body></html>'
  const hardened = hardenReportHtml(html)

  assert.match(hardened, /Content-Security-Policy/)
  assert.match(hardened, /default-src 'none'/)
  assert.match(hardened, /style-src 'unsafe-inline'/)
  assert.match(hardened, /img-src data: blob:/)
  assert.equal((hardened.match(/Content-Security-Policy/g) || []).length, 1)
  assert.ok(hardened.indexOf('Content-Security-Policy') < hardened.indexOf('<title>Brief</title>'))
})

test('hardenReportHtml wraps fragments in a complete document', async () => {
  const { hardenReportHtml } = await bundle()

  const hardened = hardenReportHtml('<main>hello</main>')

  assert.match(hardened, /^<!doctype html>/i)
  assert.match(hardened, /<body><main>hello<\/main><\/body>/)
})

test('hardenReportHtml injects height-reporter script that postMessages dreaming:brief-height', async () => {
  const { hardenReportHtml } = await bundle()

  const html = '<!doctype html><html><head><title>Brief</title></head><body><p>hi</p></body></html>'
  const hardened = hardenReportHtml(html)

  // script-src 'unsafe-inline' must be present (required for injected script)
  assert.match(hardened, /script-src 'unsafe-inline'/)
  // The height reporter script must be present
  assert.match(hardened, /dreaming:brief-height/)
  assert.match(hardened, /postMessage/)
  // The reporter must measure the documentElement border-box height —
  // viewport-independent, so a transient over-measurement can shrink back.
  assert.match(hardened, /document\.documentElement\.getBoundingClientRect\(\)\.height/)
  // scrollHeight is floored at the iframe's own viewport height, so a
  // transient over-measurement mid-reflow (classic scrollbars re-wrapping
  // text) would ratchet the iframe taller forever. The reporter must not
  // use it.
  assert.doesNotMatch(hardened, /scrollHeight/)
  // Script injected before existing head content
  assert.ok(
    hardened.indexOf('dreaming:brief-height') < hardened.indexOf('<title>Brief</title>'),
    'height reporter should appear before existing head content',
  )
})

test('hardenReportHtml injects overflow guards so a brief never scrolls horizontally', async () => {
  const { hardenReportHtml } = await bundle()

  const html = '<!doctype html><html><head><title>Brief</title></head><body><h1>Morning</h1></body></html>'
  const hardened = hardenReportHtml(html)

  // html/body boxed to the viewport, no sideways scroll
  assert.match(hardened, /html,\s*body\s*\{[^}]*overflow-x:\s*hidden/)
  assert.match(hardened, /html,\s*body\s*\{[^}]*max-width:\s*100%/)
  // box-sizing reset + media/table capped to 100%
  assert.match(hardened, /box-sizing:\s*border-box/)
  assert.match(hardened, /img,\s*svg,\s*video,\s*canvas\s*\{[^}]*max-width:\s*100%/)
  // wide tables become their own scroller instead of pushing the page wide
  assert.match(hardened, /table\s*\{[^}]*display:\s*block[^}]*overflow-x:\s*auto/)
  // long code/pre wraps rather than overflowing
  assert.match(hardened, /white-space:\s*pre-wrap/)
  assert.match(hardened, /word-break:\s*break-word/)

  // Base style must come before the brief's own head content so the template's
  // richer rules win on the cascade.
  assert.ok(
    hardened.indexOf('overflow-x: hidden') < hardened.indexOf('<title>Brief</title>'),
    'base overflow style should appear before existing head content',
  )
})

test('hardenReportHtml styles details/summary drill-down and the questions card', async () => {
  const { hardenReportHtml } = await bundle()

  const hardened = hardenReportHtml('<main>hi</main>')

  // <details>/<summary> get native-feeling chrome (so the brief can stay
  // high-level by default and reveal detail on tap)
  assert.match(hardened, /details\s*\{/)
  assert.match(hardened, /details\s*>\s*summary\s*\{/)
  assert.match(hardened, /details\[open\]\s*>\s*summary::before/)
  // the end-of-brief "questions for you" card has a styled block
  assert.match(hardened, /\.brief-questions\s*\{/)
})
