import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import test from 'node:test'

const execFileAsync = promisify(execFile)
const appRoot = dirname(dirname(fileURLToPath(import.meta.url)))

function json(response, status, value) {
  response.writeHead(status, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify(value))
}

// The wrapper owns morning-push delivery, but an instance whose live
// (agent-editable) reflection skill predates wrapper-owned delivery can still
// have the nightly agent send its own push mid-run — the skill file is seeded
// once and never overwritten by app updates, so without a guard every such
// instance announces the same brief twice, forever. Delivery must therefore be
// idempotent per day: skip when an identically-titled push already went out
// today, and fail OPEN (send) when history can't be read, because a silent
// morning is worse than a duplicate.
test('morning push is idempotent per day and fails open', async () => {
  const dataDir = await mkdtemp(join(tmpdir(), 'reflection-push-'))
  await writeFile(join(dataDir, 'service-token.txt'), 'test-service-token\n')
  await mkdir(join(dataDir, 'cron-logs'), { recursive: true })
  const runner = join(dataDir, 'runner.py')
  await writeFile(runner, 'raise SystemExit(0)\n')

  // fetch.sh names the brief with the server-local date (`date +%F`); the
  // dedupe guard compares sent_at (UTC) against the current UTC date. Both
  // sides of this test use "now", so they stay consistent in any timezone.
  const d = new Date()
  const localDate = [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
  ].join('-')
  const reports = join(dataDir, 'apps', '1', 'reports')
  await mkdir(reports, { recursive: true })
  await writeFile(join(reports, `${localDate}.html`), '<html>brief</html>\n')

  let history = []
  let historyStatus = 200
  const sends = []
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost')
    if (request.method === 'POST' && url.pathname === '/api/notifications/send') {
      let body = ''
      request.on('data', (chunk) => { body += chunk })
      request.on('end', () => {
        sends.push(JSON.parse(body))
        json(response, 200, { id: `n-${sends.length}` })
      })
      return
    }
    if (request.method === 'GET' && url.pathname === '/api/notifications') {
      if (historyStatus !== 200) {
        response.writeHead(historyStatus).end('unavailable')
        return
      }
      return json(response, 200, history)
    }
    if (request.method === 'POST' && url.pathname === '/api/admin/activity/emit') {
      request.resume()
      response.writeHead(204).end()
      return
    }
    // Everything else fetch.sh stages is best-effort; 404s are tolerated.
    request.resume()
    return json(response, 404, { detail: 'Not found' })
  })

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  const run = () => execFileAsync('bash', [join(appRoot, 'fetch.sh'), '1'], {
    cwd: appRoot,
    env: {
      ...process.env,
      API_BASE_URL: `http://127.0.0.1:${port}`,
      DATA_DIR: dataDir,
      REFLECTION_DRY: '0',
      REFLECTION_RUNNER: runner,
      REFLECTION_TIMEOUT: '5',
      REFLECTION_RESOURCE_WARN_PERCENT: '100',
      REFLECTION_RESOURCE_CRITICAL_PERCENT: '101',
      CODEX_HOME: join(dataDir, 'codex-home'),
      CLAUDE_CONFIG_DIR: join(dataDir, 'claude-home'),
    },
  })
  const readLog = () => readFile(join(dataDir, 'cron-logs', 'reflection.log'), 'utf8')

  try {
    // 1. An identically-titled push already went out today (the stale-skill
    //    agent's send, attributed to a different source): the wrapper skips.
    history = [{
      title: 'Your morning brief is ready',
      sent_at: new Date().toISOString(),
      source_type: 'agent',
      source_id: null,
    }]
    await run()
    assert.equal(sends.length, 0)
    assert.match(await readLog(), /morning push: skip \(an identical push already went out today\)/)

    // 2. Yesterday's push does not count: today's brief still gets announced.
    history = [{
      title: 'Your morning brief is ready',
      sent_at: new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString(),
      source_type: 'app',
      source_id: '1',
    }]
    await run()
    assert.equal(sends.length, 1)
    assert.equal(sends[0].title, 'Your morning brief is ready')
    assert.match(await readLog(), /morning push sent \(http=200\)/)

    // 3. History unreadable: fail open and send rather than risk silence.
    sends.length = 0
    historyStatus = 503
    await run()
    assert.equal(sends.length, 1)
  } finally {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
    await rm(dataDir, { recursive: true, force: true })
  }
})

test('dry rehearsal cannot notify or replace the last real effort receipt', async () => {
  const dataDir = await mkdtemp(join(tmpdir(), 'reflection-dry-'))
  await writeFile(join(dataDir, 'service-token.txt'), 'test-service-token\n')
  await mkdir(join(dataDir, 'cron-logs'), { recursive: true })
  await mkdir(join(dataDir, 'apps', '1', 'reports'), { recursive: true })
  await mkdir(join(dataDir, 'apps', 'reflection', 'inputs'), { recursive: true })
  const effort = join(dataDir, 'apps', 'reflection', 'inputs', 'latest-effort.json')
  await writeFile(effort, '{"real":true}\n')
  const d = new Date()
  const localDate = [
    d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
  ].join('-')
  await writeFile(
    join(dataDir, 'apps', '1', 'reports', `${localDate}.html`),
    '<html>existing brief</html>\n',
  )
  let sends = 0
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost')
    if (request.method === 'POST' && url.pathname === '/api/notifications/send') sends += 1
    request.resume()
    return json(response, 404, { detail: 'Not found' })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  try {
    await execFileAsync('bash', [join(appRoot, 'fetch.sh'), '1'], {
      cwd: appRoot,
      env: {
        ...process.env,
        API_BASE_URL: `http://127.0.0.1:${port}`,
        DATA_DIR: dataDir,
        REFLECTION_DRY: '1',
        REFLECTION_TIMEOUT: '5',
        REFLECTION_RESOURCE_WARN_PERCENT: '100',
        REFLECTION_RESOURCE_CRITICAL_PERCENT: '101',
        CODEX_HOME: join(dataDir, 'codex-home'),
        CLAUDE_CONFIG_DIR: join(dataDir, 'claude-home'),
      },
    })
    assert.equal(sends, 0)
    assert.equal(await readFile(effort, 'utf8'), '{"real":true}\n')
    assert.match(
      await readFile(join(dataDir, 'cron-logs', 'reflection.log'), 'utf8'),
      /morning push: skip \(dry run\)/,
    )
  } finally {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
    await rm(dataDir, { recursive: true, force: true })
  }
})
