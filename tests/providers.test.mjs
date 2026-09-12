import assert from 'node:assert/strict'
import test from 'node:test'
import { fetchModelConfig } from '../providers.js'


test('model config follows the canonical configured provider status', async () => {
  const previousFetch = globalThis.fetch
  globalThis.fetch = async (url) => new Response(JSON.stringify(
    String(url).endsWith('/status')
      ? {
          claude: { configured: true },
          codex: { configured: false, authenticated: true },
        }
      : {
          claude: [{ id: 'claude-model', name: 'Claude model' }],
          codex: [{ id: 'codex-model', name: 'Codex model' }],
        },
  ), { status: 200, headers: { 'Content-Type': 'application/json' } })

  try {
    const config = await fetchModelConfig('owner-token')
    assert.deepEqual([...config.connected], ['claude'])
  } finally {
    globalThis.fetch = previousFetch
  }
})
