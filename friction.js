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

export function frictionRows(frictionText, outcomesText, runs = []) {
  const reports = new Set(runs.filter((run) => run.report).map((run) => run.id))
  const outcomes = new Map(lines(outcomesText)
    .filter((outcome) => !outcome.run || reports.has(outcome.run))
    .map((outcome) => [outcome.friction_id, outcome]))
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

// Sorts the friction log into work waiting for a run, proposals in reports,
// and outcomes that need no owner decision.
// `working` is true while a run's chat is actually running.
export function backlogSections(rows, runs) {
  const working = (runs || []).some((run) => run.running)
  const sections = { pending: [], decision: [], done: [], working }
  for (const row of rows) {
    if (!row.outcome) sections.pending.push(row)
    else if (row.outcome.outcome === 'asked') sections.decision.push(row)
    else sections.done.push(row)
  }
  return sections
}
