// Joins the friction log with Reflection's outcomes, newest first. Both files
// are JSON Lines; a partially written line is skipped until it completes.

function lines(text) {
  const out = []
  for (const line of String(text || '').split('\n')) {
    try {
      const value = JSON.parse(line)
      if (value && typeof value === 'object') out.push(value)
    } catch {}
  }
  return out
}

export function frictionRows(frictionText, outcomesText) {
  const outcomes = new Map(lines(outcomesText).map((o) => [o.friction_id, o]))
  return lines(frictionText)
    .filter((entry) => typeof entry.friction === 'string')
    .map((entry) => ({ ...entry, outcome: outcomes.get(entry.id) || null }))
    .reverse()
}

// A chat the daily job opened (the app's other chats are not runs).
export function isRunChat(chat) {
  return /^run-/.test(chat?.scope || '')
}

// The report's `# ` headline, if it has one.
export function reportHeadline(markdown) {
  const match = /^#\s+(.+)$/m.exec(String(markdown || ''))
  return match ? match[1].trim() : ''
}

// Sorts the friction log into what the owner needs to see: work waiting for
// (or in) a run, causes that await the owner's decision, and what is done.
// `working` is true while a run's chat is actually running.
export function backlogSections(rows, runs) {
  const byId = new Map((runs || []).map((run) => [run.id, run]))
  const working = (runs || []).some((run) => run.running)
  const sections = { pending: [], decision: [], done: [], working }
  for (const row of rows) {
    if (!row.outcome) sections.pending.push(row)
    else if (byId.get(row.outcome.run)?.awaiting_owner) sections.decision.push(row)
    else sections.done.push(row)
  }
  return sections
}
