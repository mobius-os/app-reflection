import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { delimiter, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import test from 'node:test'

const execFileAsync = promisify(execFile)
const appRoot = dirname(dirname(fileURLToPath(import.meta.url)))

function json(response, status, value) {
  response.writeHead(status, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify(value))
}

test('fetch stages an exact activity snapshot and fails closed while retaining it', async () => {
  const dataDir = await mkdtemp(join(tmpdir(), 'reflection-fetch-'))
  await writeFile(join(dataDir, 'service-token.txt'), 'test-service-token\n')
  const cronLogs = join(dataDir, 'cron-logs')
  await mkdir(cronLogs, { recursive: true })
  const legacyNoise = [
    '[2026-07-21T06:00:00+00:00] reflection: start (app_id=1)',
    '  · codex {"type": "tool_start", "tool": "Bash", "input": "inspect"}',
    ...Array.from({ length: 180 }, (_, i) => (
      `  · codex {"type": "tool_output", "content": "legacy-noise-${i}-${'x'.repeat(400)}`
    )),
    '  · codex tool_result tool=Bash preview={"type": "tool_output"}',
    ...Array.from({ length: 40 }, (_, i) => `  > prose-delta-${i}`),
    '[2026-07-21T06:10:00+00:00] reflection_runner: done',
  ].join('\n') + '\n'
  await writeFile(join(cronLogs, 'reflection.log'), legacyNoise)
  const chatId = 'chat-with-note'
  const chatNote = join(dataDir, 'shared', 'memory', 'chats', chatId, 'index.md')
  await mkdir(dirname(chatNote), { recursive: true })
  await writeFile(chatNote, 'bounded note\n')
  let failActivity = false
  const now = new Date().toISOString()
  const activity = [
    { ev: 'app_open', ts: now, app_id: 1 },
    { ev: 'app_error', ts: now, app_id: 1, message: 'render failed', where: 'canvas' },
    {
      ev: 'request_error', ts: now, app_id: 1, method: 'GET',
      route: '/api/storage/apps/{app_id}/{path:path}', status: 404,
      count: 1320, first_ts: now, last_ts: now, duration_ms: 59900,
    },
    {
      ev: 'request_error', ts: now, app_id: 1, method: 'POST',
      route: '/api/apps/{app_id}/compile', status: 500,
      count: 2, first_ts: now, last_ts: now, duration_ms: 200,
    },
    {
      ev: 'app_signal', ts: now, app_id: 1, id: 'signal-1',
      occurred_at: now, name: 'item_created', payload: { type: 'note' },
    },
  ].map((event) => JSON.stringify(event)).join('\n') + '\n'

  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost')
    if (request.method === 'POST' && url.pathname === '/api/admin/activity/emit') {
      request.resume()
      response.writeHead(204).end()
      return
    }
    if (url.pathname === '/api/admin/activity') {
      if (failActivity) {
        response.writeHead(503).end('temporarily unavailable')
      } else {
        response.writeHead(200, { 'Content-Type': 'application/x-ndjson' })
        response.end(activity)
      }
      return
    }
    if (url.pathname === '/api/chats') return json(response, 200, [{
      id: chatId, title: 'Useful session', provider: 'codex',
      updated_at: now, message_count: 7,
    }])
    if (url.pathname === '/api/apps/') {
      return json(response, 200, [{ id: 1, name: 'reflection', display_name: 'Reflection' }])
    }
    if (url.pathname.startsWith('/api/storage/shared-list/')) {
      return json(response, 200, { entries: [], next_cursor: null })
    }
    if (url.pathname.startsWith('/api/storage/apps-list/')) {
      return json(response, 200, { entries: [], next_cursor: null })
    }
    if (url.pathname === '/api/storage/apps/1/signals.jsonl') {
      return json(response, 404, { detail: 'Not found' })
    }
    return json(response, 404, { detail: 'Not found' })
  })

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  const run = (overrides = {}) => execFileAsync('bash', [join(appRoot, 'fetch.sh'), '1'], {
    cwd: appRoot,
    env: {
      ...process.env,
      API_BASE_URL: `http://127.0.0.1:${port}`,
      DATA_DIR: dataDir,
      REFLECTION_DRY: '1',
      REFLECTION_TIMEOUT: '5',
      REFLECTION_LOG_MAX_BYTES: '4096',
      REFLECTION_RESOURCE_WARN_PERCENT: '100',
      REFLECTION_RESOURCE_CRITICAL_PERCENT: '101',
      CODEX_HOME: join(dataDir, 'codex-home'),
      CLAUDE_CONFIG_DIR: join(dataDir, 'claude-home'),
      ...overrides,
    },
  })

  try {
    await run()
    const inputs = join(dataDir, 'apps', 'reflection', 'inputs')
    const snapshot = await readFile(join(inputs, 'activity.jsonl'), 'utf8')
    const status = JSON.parse(await readFile(join(inputs, 'activity-status.json'), 'utf8'))
    const digest = JSON.parse(await readFile(join(inputs, 'per-app-digest.json'), 'utf8'))
    const resources = JSON.parse(await readFile(join(inputs, 'resource-snapshot.json'), 'utf8'))
    const memoryHealth = JSON.parse(await readFile(join(inputs, 'memory-health.json'), 'utf8'))
    const resourceHistory = await readFile(join(dataDir, 'apps', 'reflection', 'resource-history.jsonl'), 'utf8')
    const stagedResourceHistory = await readFile(join(inputs, 'resource-history.jsonl'), 'utf8')
    const runHistory = await readFile(join(inputs, 'reflection-run-history.txt'), 'utf8')
    const archivedLog = await readFile(join(cronLogs, 'reflection.log.1'), 'utf8')
    const currentLog = await readFile(join(cronLogs, 'reflection.log'), 'utf8')
    const metaState = await readFile(join(inputs, 'meta-state.md'), 'utf8')
    const metaStateStatus = JSON.parse(await readFile(join(inputs, 'meta-state-status.json'), 'utf8'))
    const metaLearning = await readFile(join(inputs, 'meta-learning.jsonl'), 'utf8')
    const chats = await readFile(join(inputs, 'chats.md'), 'utf8')
    assert.equal(snapshot, activity)
    assert.equal(status.ok, true)
    assert.equal(status.event_count, 5)
    assert.equal(status.sha256, createHash('sha256').update(activity).digest('hex'))
    assert.equal(digest.activity_source.ok, true)
    assert.equal(digest.apps[0].opens_24h, 1)
    assert.equal(digest.apps[0].signal_counts.item_created, 1)
    assert.equal(digest.apps[0].app_errors_24h, 1)
    assert.equal(digest.apps[0].recent_app_errors[0].message, 'render failed')
    assert.equal(digest.apps[0].request_errors_24h, 2)
    assert.deepEqual(digest.apps[0].top_request_errors[0], {
      method: 'POST',
      route: '/api/apps/{app_id}/compile',
      status: 500,
      count: 2,
      peak_window_count: 2,
      first_ts: now,
      last_ts: now,
    })
    assert.equal(resources.version, 2)
    assert.equal(resources.filesystems.data_volume.scope, 'data-volume')
    assert.equal(resources.filesystems.container_root.scope, 'container-root')
    assert.equal(memoryHealth.available, false)
    assert.equal(memoryHealth.writer_contract.reflection_may_write_graph, false)
    assert.equal(resources.deep_scan.ran, true)
    assert.equal(resourceHistory.trim().split('\n').length, 1)
    assert.equal(stagedResourceHistory, resourceHistory)
    assert.match(runHistory, /no prior metrics/)
    assert.match(runHistory, /Recent reflection log tail \(normalized\)/)
    assert.match(runHistory, /legacy tool_output stream: 180 chunks omitted/)
    assert.match(runHistory, /prose-delta-0 prose-delta-1/)
    assert.doesNotMatch(runHistory, /"content": "legacy-noise/)
    assert.match(runHistory, /tool_result tool=Bash preview=\{"type": "tool_output"\}/)
    assert.match(archivedLog, /legacy-noise-179/)
    assert.doesNotMatch(currentLog, /legacy-noise/)
    assert.match(metaState, /Reflection operating model/)
    assert.equal(metaStateStatus.exists, true)
    assert.equal(metaStateStatus.first_run_seed, true)
    assert.equal(metaLearning, '')
    assert.match(chats, /messages=7, note_bytes=13/)

    const learning = JSON.stringify({
      ts: '2026-07-17T00:00:00Z',
      evidence: 'Repeated broad checks produced no new finding',
      inference: 'A due date is a better trigger than a nightly ritual',
      change: 'Added adaptive review cadence',
      revisit_after: '2026-07-24',
    })
    await writeFile(join(dataDir, 'apps', 'reflection', 'meta-learning.jsonl'), `${learning}\nnot-json\n`)

    failActivity = true
    await run()
    const retained = await readFile(join(inputs, 'activity.jsonl'), 'utf8')
    const failedStatus = JSON.parse(await readFile(join(inputs, 'activity-status.json'), 'utf8'))
    const failedDigest = JSON.parse(await readFile(join(inputs, 'per-app-digest.json'), 'utf8'))
    const nextResources = JSON.parse(await readFile(join(inputs, 'resource-snapshot.json'), 'utf8'))
    const nextRunHistory = await readFile(join(inputs, 'reflection-run-history.txt'), 'utf8')
    const nextMetaLearning = await readFile(join(inputs, 'meta-learning.jsonl'), 'utf8')
    assert.equal(retained, activity)
    assert.equal(failedStatus.ok, false)
    assert.equal(failedStatus.retained_previous_snapshot, true)
    assert.match(failedStatus.error, /activity fetch failed/)
    assert.equal(failedDigest.activity_source.ok, false)
    assert.equal(failedDigest.apps[0].opens_24h, 0)
    assert.equal(failedDigest.apps[0].app_errors_24h, 0)
    assert.equal(failedDigest.apps[0].request_errors_24h, 0)
    assert.equal(nextResources.deep_scan.ran, false)
    assert.equal(nextResources.deep_scan.reason, 'not-due')
    assert.match(nextRunHistory, /"exit_code":0/)
    assert.equal(nextMetaLearning, `${learning}\n`)

    // A broken primary archive target used to leave the oversized current log
    // in place, so every retry appended forever. A directory is the persistent
    // failure shape observed by the reviewer. The fixed fallback archive is
    // replaced on every retry, and each run starts a fresh bounded current log.
    const primaryArchive = join(cronLogs, 'reflection.log.1')
    await rm(primaryArchive, { recursive: true, force: true })
    await mkdir(primaryArchive)
    await writeFile(
      join(cronLogs, 'reflection.log'),
      `persistent-rotation-evidence ${'r'.repeat(2_048)}\n`,
    )
    const currentSizes = []
    for (let i = 0; i < 4; i += 1) {
      await run({ REFLECTION_LOG_MAX_BYTES: '1' })
      const current = await readFile(join(cronLogs, 'reflection.log'))
      currentSizes.push(current.byteLength)
      if (i === 0) {
        const fallbackHistory = await readFile(
          join(inputs, 'reflection-run-history.txt'), 'utf8',
        )
        assert.match(fallbackHistory, /persistent-rotation-evidence/)
      }
    }
    assert.ok(currentSizes.every((size) => size < 2_048), currentSizes.join(','))
    const fallbackArchive = await readFile(
      join(cronLogs, 'reflection.log.rotation-fallback'), 'utf8',
    )
    assert.ok(fallbackArchive.length < 2_048)

    // Exercise GNU timeout against the real runner, not a direct close() fake.
    // The fake Codex provider leaves one tool in flight; SIGTERM must cancel
    // the asyncio task so _LogBroadcast.close() can preserve its true tail,
    // while the wrapper still records/returns timeout's canonical rc=124.
    const fakeBackend = join(dataDir, 'fake-backend')
    const fakeApp = join(fakeBackend, 'app')
    const fakeScripts = join(fakeBackend, 'scripts')
    await mkdir(fakeApp, { recursive: true })
    await mkdir(fakeScripts, { recursive: true })
    await writeFile(join(fakeApp, '__init__.py'), '')
    await writeFile(join(fakeApp, 'background_agents.py'), `
def resolve_background_agents(data_dir, settings):
    return {"primary": {"provider": "codex", "model": None, "effort": None}, "fallback": None}
`)
    await writeFile(join(fakeApp, 'codex_sdk_runner.py'), `
import asyncio

async def run_codex_sdk_turn(**kwargs):
    bc = kwargs["bc"]
    bc.publish({"type": "session_init", "session_id": "term-session"})
    bc.publish({"type": "tool_start", "tool": "Bash", "input": "wait", "tool_use_id": "term-tool"})
    bc.publish({"type": "tool_output", "content": ("x" * 5000) + " UNIQUE-TERM-CONCLUSION", "tool_use_id": "term-tool"})
    await asyncio.Event().wait()
`)
    const skillDir = join(dataDir, 'shared', 'skills')
    await mkdir(skillDir, { recursive: true })
    await writeFile(join(skillDir, 'reflection.md'), 'Test reflection skill.\n')
    const fakeRunner = join(fakeScripts, 'reflection_runner.py')
    await copyFile(join(appRoot, 'reflection_runner.py'), fakeRunner)
    let timeoutError
    try {
      await run({
        REFLECTION_DRY: '0',
        // The real runner takes a best-effort pre-run safety snapshot before
        // it starts the provider. Leave enough headroom for that unrelated
        // host work so this test deterministically reaches the fake provider
        // and exercises cancellation of its in-flight tool.
        REFLECTION_TIMEOUT: '3',
        REFLECTION_LOG_MAX_BYTES: '1048576',
        REFLECTION_RUNNER: fakeRunner,
        PYTHONPATH: [fakeBackend, process.env.PYTHONPATH].filter(Boolean).join(delimiter),
      })
    } catch (error) {
      timeoutError = error
    }
    assert.equal(timeoutError?.code, 124)
    const timeoutLog = await readFile(join(cronLogs, 'reflection.log'), 'utf8')
    assert.match(timeoutLog, /SIGTERM received; cancelling Reflection/)
    assert.match(timeoutLog, /id=term-tool state=incomplete/)
    assert.match(timeoutLog, /UNIQUE-TERM-CONCLUSION/)
    assert.match(timeoutLog, /agent run hit the 3s timeout/)
    assert.match(timeoutLog, /done \(rc=124\)/)
  } finally {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
    await rm(dataDir, { recursive: true, force: true })
  }
})
