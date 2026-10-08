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
  // `hasReport` covers older runs whose report text is loaded only on demand.
  const reports = new Set(runs.filter((run) => run.report || run.hasReport).map((run) => run.id))
  const outcomes = new Map(lines(outcomesText)
    .filter((outcome) => !outcome.run || reports.has(outcome.run))
    .map((outcome) => [outcome.friction_id, outcome]))
  return lines(frictionText)
    .filter((entry) => typeof entry.friction === 'string')
    .map((entry) => ({ ...entry, outcome: outcomes.get(entry.id) || null }))
    .reverse()
}

// Older runs' reports are read only when opened. This remembers each read
// across refreshes: the text, null when the run had no report, or a failure
// the view offers to retry. A successful reports listing (`listed` true or
// false; null when unavailable) owns presence, so a remembered "no report"
// gives way to a report listed later.
export function createOlderReports() {
  const texts = new Map()
  const failed = new Set()
  function fields(runId, listed) {
    if (listed === false) texts.delete(runId)
    const known = texts.get(runId)
    // Without a listing or a read, assume a report exists so its outcomes stay.
    if (!(listed ?? known !== null)) return { report: null, hasReport: false, reportError: false }
    if (typeof known === 'string') return { report: known, hasReport: true, reportError: false }
    return { report: undefined, hasReport: true, reportError: failed.has(runId) }
  }
  // `readText` resolves the text or null for no report, and throws when the
  // read could not answer.
  async function read(runId, readText) {
    failed.delete(runId)
    try {
      texts.set(runId, await readText())
    } catch {
      failed.add(runId)
    }
    return fields(runId, null)
  }
  return { fields, read }
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
