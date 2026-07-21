import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
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

test('fetch stages an exact activity snapshot and fails closed while retaining it', async () => {
  const dataDir = await mkdtemp(join(tmpdir(), 'reflection-fetch-'))
  await writeFile(join(dataDir, 'service-token.txt'), 'test-service-token\n')
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
  const run = () => execFileAsync('bash', [join(appRoot, 'fetch.sh'), '1'], {
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
    const metaState = await readFile(join(inputs, 'meta-state.md'), 'utf8')
    const metaStateStatus = JSON.parse(await readFile(join(inputs, 'meta-state-status.json'), 'utf8'))
    const metaLearning = await readFile(join(inputs, 'meta-learning.jsonl'), 'utf8')
    const chats = await readFile(join(inputs, 'chats.md'), 'utf8')
    assert.equal(snapshot, activity)
    assert.equal(status.ok, true)
    assert.equal(status.event_count, 3)
    assert.equal(status.sha256, createHash('sha256').update(activity).digest('hex'))
    assert.equal(digest.activity_source.ok, true)
    assert.equal(digest.apps[0].opens_24h, 1)
    assert.equal(digest.apps[0].signal_counts.item_created, 1)
    assert.equal(digest.apps[0].app_errors_24h, 1)
    assert.equal(digest.apps[0].recent_app_errors[0].message, 'render failed')
    assert.equal(resources.version, 2)
    assert.equal(resources.filesystems.data_volume.scope, 'data-volume')
    assert.equal(resources.filesystems.container_root.scope, 'container-root')
    assert.equal(memoryHealth.available, false)
    assert.equal(memoryHealth.writer_contract.reflection_may_write_graph, false)
    assert.equal(resources.deep_scan.ran, true)
    assert.equal(resourceHistory.trim().split('\n').length, 1)
    assert.equal(stagedResourceHistory, resourceHistory)
    assert.match(runHistory, /no prior metrics/)
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
    assert.equal(nextResources.deep_scan.ran, false)
    assert.equal(nextResources.deep_scan.reason, 'not-due')
    assert.match(nextRunHistory, /"exit_code":0/)
    assert.equal(nextMetaLearning, `${learning}\n`)
  } finally {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
    await rm(dataDir, { recursive: true, force: true })
  }
})
