// Reflection: each run's report (with its chat, where the agent asks before
// fixing anything), the friction backlog, and when and with which agent it runs.
import { useCallback, useEffect, useRef, useState } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { ArrowLeft, Chat, ChevronRight, Lightbulb, Play, Reload } from '@openai/apps-sdk-ui/components/Icon'
import { backlogSections, frictionRows, isRunChat, reportHeadline } from './friction.js'
import { dailyCron, scheduleTime } from './schedule.js'

// Shapes follow the shared app-component catalog (mobius-ui:* labels).
const CSS = `
  .ma-root { box-sizing: border-box; position: relative; min-height: 100dvh; overflow-x: clip;
    background: var(--bg); color: var(--text); font-family: var(--font); -webkit-font-smoothing: antialiased; }
  /* One centered 760px column, header included, like Skills and Integrations. */
  .ma-header { position: sticky; top: 0; z-index: 2; background: var(--bg); }
  .ma-header-inner { max-width: 760px; margin-inline: auto; display: flex; align-items: center; gap: 12px; min-height: 48px;
    padding: max(12px, env(safe-area-inset-top)) 16px 12px; border-bottom: 1px solid var(--border); }
  .ma-brand { display: flex; align-items: center; gap: 11px; min-width: 0; }
  .ma-mark { flex: 0 0 auto; width: 34px; height: 34px; border-radius: 8px; object-fit: cover; }
  .ma-brand-text { min-width: 0; line-height: 1.15; }
  .ma-title { margin: 0; font-size: 18px; font-weight: 700; }
  .ma-subtitle { display: block; margin-top: 1px; font-size: 12px; color: var(--muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
  .rf-page { max-width: 760px; margin: 0 auto; padding: 16px 16px 48px; }
  .ma-seg { display: flex; gap: 2px; height: 44px; margin-bottom: 18px; background: var(--surface-2, var(--surface));
    border-radius: 10px; box-shadow: inset 0 0 0 1px var(--border); }
  .ma-seg-btn { flex: 1; box-sizing: border-box; min-height: 44px; padding: 6px 14px; border: 0; border-radius: 7px;
    background: transparent; color: var(--muted); font-family: var(--font); font-size: 13px; font-weight: 650; cursor: pointer;
    transition: background .15s, color .15s; }
  .ma-seg-btn:hover { color: var(--text); }
  .ma-seg-btn.is-active { background: var(--accent); color: var(--accent-fg); }
  .ma-card { box-sizing: border-box; display: flex; align-items: center; gap: 14px; width: 100%; min-height: 44px; padding: 15px 16px;
    text-align: left; background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 12px;
    font-family: var(--font); transition: border-color .16s ease, transform .12s ease; }
  button.ma-card { cursor: pointer; }
  button.ma-card:hover { border-color: color-mix(in srgb, var(--accent) 60%, var(--border)); }
  button.ma-card:active { transform: scale(.992); }
  .ma-card.is-featured { border-left: 3px solid var(--accent); }
  .ma-card-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .ma-card-title { font-size: 15px; font-weight: 700; letter-spacing: -0.01em; line-height: 1.35; }
  .ma-card-sub { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; font-weight: 500; color: var(--muted); }
  .ma-card-chevron { flex: 0 0 auto; font-size: 20px; color: var(--muted); opacity: .7; }
  .rf-item { flex-direction: column; align-items: stretch; gap: 8px; }
  .rf-text { margin: 0; font-size: 14px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
  .rf-note { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); }
  .rf-tag { padding: 2px 9px; border-radius: 999px; font-weight: 650; border: 1px solid var(--border); }
  .rf-tag.is-accent { color: var(--accent); border-color: var(--accent); }
  .rf-actions { display: flex; flex-wrap: wrap; gap: 4px; margin: 0 -10px -8px; }
  .rf-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
  .rf-section { margin: 0 0 26px; }
  .rf-section h2 { margin: 0 0 10px; font-size: 12px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); }
  .rf-quiet { margin: 0; padding: 4px 2px; font-size: 14px; color: var(--muted); }
  .rf-report-error { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
  .ma-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 8px;
    min-height: 50dvh; max-width: 440px; margin: 0 auto; padding: 32px 24px; color: var(--muted); }
  .ma-empty-mark { width: 64px; height: 64px; margin-bottom: 10px; border-radius: 18px; display: flex; align-items: center; justify-content: center;
    color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border)); font-size: 30px; }
  .ma-empty-title { font-size: 17px; font-weight: 700; color: var(--text); letter-spacing: -0.01em; }
  .ma-empty-text { margin: 0; font-size: 14px; line-height: 1.6; }
  .ma-btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 44px; padding: 10px 16px;
    border-radius: 10px; border: 1px solid var(--border); background: var(--surface); color: var(--text); font-family: var(--font);
    font-size: 14px; font-weight: 600; cursor: pointer; white-space: nowrap; transition: background .14s ease, transform .1s ease; }
  .ma-btn svg { font-size: 18px; }
  .ma-btn:active { transform: scale(.97); }
  .ma-btn:disabled { opacity: .5; cursor: default; transform: none; }
  .ma-btn-primary { background: var(--accent); border-color: var(--accent); color: var(--accent-fg); }
  .ma-btn-secondary { background: var(--surface-2, var(--surface)); }
  .ma-btn-ghost { background: transparent; border-color: transparent; color: var(--accent); padding: 10px; }
  .ma-btn-ghost:hover { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .ma-btn:focus-visible, .ma-card:focus-visible, .ma-seg-btn:focus-visible, .ma-input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .ma-input { box-sizing: border-box; width: 100%; min-height: 44px; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--surface); color: var(--text); font: 15px var(--font); }
  .rf-back { margin: 0 0 8px -10px; }
  .rf-report { font-size: 15px; line-height: 1.6; overflow-wrap: anywhere; }
  .rf-report h1 { font-size: 20px; letter-spacing: -0.01em; margin: 4px 0 12px; }
  .rf-report h2 { font-size: 16px; margin: 22px 0 8px; }
  .rf-report p, .rf-report ul, .rf-report ol { margin: 0 0 12px; }
  .rf-report li { margin: 4px 0; }
  .rf-report code { font-size: 13px; }
  .rf-chat { margin-top: 22px; height: 70vh; min-height: 420px; border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
  .rf-form { display: flex; flex-direction: column; gap: 20px; }
  .rf-label { display: flex; flex-direction: column; gap: 6px; font-size: 14px; font-weight: 650; }
  .rf-hint { font-size: 13px; font-weight: 400; color: var(--muted); line-height: 1.5; }
  .rf-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  .rf-status { font-size: 13px; color: var(--muted); }
  .rf-loading { padding: 40px 4px; text-align: center; color: var(--muted); font-size: 14px; }
`

const OUTCOME_LABELS = {
  asked: 'Proposed',
  resolved: 'Resolved since',
  joined: 'Same cause as another',
  explained: 'Explained',
}
const PROVIDERS = { claude: 'Claude', codex: 'Codex' }
const TABS = [['reports', 'Reports'], ['backlog', 'Backlog'], ['settings', 'Settings']]

function when(iso) {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  return at.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function api(token, path, options = {}) {
  return fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...options.headers },
  })
}

// Opens a chat in the owner's workspace. App frames have an opaque origin, so
// `*` is required for this one hop; the shell accepts it only from this frame.
function openChat(chatId) {
  window.parent.postMessage({ type: 'moebius:open-chat', chatId }, '*')
}

// The live file; the runtime's cached copy only when offline. (Its cached read
// answers with the last-seen copy first, which hides new friction and reports.)
// Null means the file does not exist; a read that could not answer throws, so
// callers that remember results never mistake an outage for "no file".
async function readTextOrThrow(appId, token, path) {
  try {
    const r = await api(token, `/api/storage/apps/${appId}/${path}`, { cache: 'no-store' })
    if (r.status === 404) return null
    if (r.ok) return await r.text()
  } catch {}
  const cached = await window.mobius?.storage?.getText?.(path).catch(() => null)
  if (typeof cached === 'string') return cached
  throw new Error(`Could not read ${path}.`)
}

// For views that are refreshed often anyway: an unavailable file reads as absent.
function readText(appId, token, path) {
  return readTextOrThrow(appId, token, path).catch(() => null)
}

// Reports are read for the newest runs only; older ones load when opened.
// A refresh (every visibility change, and every 15 s while a run works) would
// otherwise read one file per run ever made.
const RECENT_REPORTS = 10

// Names of the saved reports, so older runs can say whether they have one
// without reading it. Null when the listing is unavailable.
async function reportNames(appId, token) {
  const names = new Set()
  let cursor = ''
  for (let page = 0; page < 20; page += 1) {
    const query = `limit=500${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`
    let body
    try {
      const r = await api(token, `/api/storage/apps-list/${appId}/reports/?${query}`, { cache: 'no-store' })
      if (r.status === 404) return names
      if (!r.ok) return null
      body = await r.json()
    } catch {
      return null
    }
    for (const entry of body?.entries || []) {
      if (entry?.type === 'file' && typeof entry?.name === 'string') names.add(entry.name)
    }
    cursor = body?.next_cursor || ''
    if (!cursor) return names
  }
  return null
}

// Runs (the chats the daily job opened) with their reports, the friction log
// joined with outcomes, and the next run time. All reload together; while a
// run is working the view refreshes so its status and report appear.
// A run's `report` is its text, null when it has none, or undefined while not
// yet read; `hasReport` is known from the listing even before the read.
function useData(appId, token) {
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)
  const olderReports = useRef(new Map())
  const load = useCallback(async () => {
    try {
      const [chatsResponse, friction, outcomes, schedules, saved] = await Promise.all([
        api(token, '/api/app-chats'),
        readText(appId, token, 'friction.jsonl'),
        readText(appId, token, 'outcomes.jsonl'),
        api(token, '/api/apps/schedules').then((r) => (r.ok ? r.json() : [])).catch(() => []),
        reportNames(appId, token),
      ])
      if (!chatsResponse.ok) throw new Error(String(chatsResponse.status))
      const chats = await chatsResponse.json()
      const runs = await Promise.all(chats.filter(isRunChat)
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .map(async (chat, index) => {
          const listed = saved ? saved.has(`${chat.id}.md`) : null
          if (index < RECENT_REPORTS) {
            const report = await readText(appId, token, `reports/${chat.id}.md`)
            return { ...chat, report, hasReport: Boolean(report) }
          }
          const report = olderReports.current.get(chat.id)
          if (report !== undefined) return { ...chat, report, hasReport: Boolean(report) }
          // Without a listing, assume a report exists so its outcomes stay.
          return { ...chat, report: undefined, hasReport: listed ?? true }
        }))
      const schedule = schedules.find((s) => s.id === Number(appId)) || null
      setData({ runs, rows: frictionRows(friction, outcomes, runs), schedule })
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [appId, token])
  // Only a definite answer (the text, or null for no report) is remembered; a
  // failed read marks the run so the view offers a retry, and reopening the
  // report reads again.
  const loadReport = useCallback(async (runId) => {
    const update = (patch) => setData((current) => current && {
      ...current,
      runs: current.runs.map((run) => (run.id === runId ? { ...run, ...patch } : run)),
    })
    update({ reportError: false })
    let report
    try {
      report = await readTextOrThrow(appId, token, `reports/${runId}.md`)
    } catch {
      update({ reportError: true })
      return
    }
    olderReports.current.set(runId, report)
    update({ report, hasReport: Boolean(report), reportError: false })
  }, [appId, token])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [load])
  const running = data?.runs.some((run) => run.running)
  useEffect(() => {
    if (!running) return undefined
    const timer = setInterval(load, 15000)
    return () => clearInterval(timer)
  }, [running, load])
  return [data, failed, load, loadReport]
}

function statusLine({ runs, rows, schedule }) {
  const runTime = scheduleTime(schedule)
  const parts = []
  if (runs.some((run) => run.running)) parts.push('Working now')
  const waiting = rows.filter((row) => !row.outcome).length
  if (waiting) parts.push(`${waiting} waiting`)
  if (runTime) parts.push(`Next run ${runTime}`)
  if (!parts.length) return 'All caught up'
  return parts.map((part, i) => <span key={i}>{i ? ' · ' : ''}{part}</span>)
}

function Header({ appId, status }) {
  return (
    <header className="ma-header">
      <div className="ma-header-inner">
        <div className="ma-brand">
          <img className="ma-mark" src={`/api/apps/${appId}/icon?size=64`} alt="" aria-hidden="true" />
          <div className="ma-brand-text">
            <h1 className="ma-title">Reflection</h1>
            {status && <span className="ma-subtitle">{status}</span>}
          </div>
        </div>
      </div>
    </header>
  )
}

function Empty({ title, children }) {
  return (
    <div className="ma-empty">
      <div className="ma-empty-mark" aria-hidden="true"><Lightbulb /></div>
      <div className="ma-empty-title">{title}</div>
      <p className="ma-empty-text">{children}</p>
    </div>
  )
}

function RunChat({ chatId }) {
  const mount = useRef(null)
  useEffect(() => {
    let handle = null
    let gone = false
    Promise.resolve(window.mobius?.chat?.({ mount: mount.current, chatId, picker: false }))
      .then((h) => { if (gone) h?.destroy?.(); else handle = h })
      .catch(() => {})
    return () => { gone = true; handle?.destroy?.() }
  }, [chatId])
  return <div ref={mount} className="rf-chat" aria-label="Reflection chat" />
}

function Report({ run, onBack, loadReport }) {
  const pending = run.report === undefined
  useEffect(() => { if (pending) loadReport(run.id) }, [pending, run.id, loadReport])
  const html = run.report ? DOMPurify.sanitize(marked.parse(run.report)) : ''
  return (
    <article>
      <button type="button" className="ma-btn ma-btn-ghost rf-back" onClick={onBack}><ArrowLeft /> Reports</button>
      {html
        ? <div className="rf-report" dangerouslySetInnerHTML={{ __html: html }} />
        : pending && run.reportError
          ? (
            <div className="rf-report-error" role="alert">
              <p className="rf-quiet">This report could not be loaded.</p>
              <button type="button" className="ma-btn ma-btn-secondary" onClick={() => loadReport(run.id)}>Try again</button>
            </div>
          )
          : pending
          ? <p className="rf-quiet" role="status">Loading report…</p>
          : <p className="rf-quiet">{run.running ? 'This run is still working. Its report will appear here.' : 'This run ended without a report.'}</p>}
      <RunChat chatId={run.id} />
    </article>
  )
}

function runTag(run, proposals) {
  if (run.awaiting_owner) return <span className="rf-tag is-accent">Needs your answer</span>
  if (run.running) return <span className="rf-tag">Working</span>
  if (!run.report && !run.hasReport) return <span className="rf-tag">No report</span>
  if (proposals.has(run.id)) return <span className="rf-tag is-accent">Fix proposed</span>
  return null
}

function Reports({ runs, rows, onOpen }) {
  const proposals = new Set(rows.filter((row) => row.outcome?.outcome === 'asked').map((row) => row.outcome.run))
  if (!runs.length) {
    return (
      <Empty title="No reports yet">
        When agents have logged friction, Reflection looks into it at the daily
        run time and reports here, asking before it fixes anything.
      </Empty>
    )
  }
  return (
    <ul className="rf-list" aria-label="Reports">
      {runs.map((run) => (
        <li key={run.id}>
          <button type="button" className={`ma-card${proposals.has(run.id) || run.awaiting_owner ? ' is-featured' : ''}`} onClick={() => onOpen(run)}>
            <div className="ma-card-main">
              <div className="ma-card-title">{reportHeadline(run.report) || run.title}</div>
              <div className="ma-card-sub">{runTag(run, proposals)}<span>{when(run.created_at)}</span></div>
            </div>
            <ChevronRight className="ma-card-chevron" aria-hidden="true" />
          </button>
        </li>
      ))}
    </ul>
  )
}

function FrictionItem({ row, run, onOpen }) {
  const chatId = row.call?.chat_id
  return (
    <li className="ma-card rf-item">
      <p className="rf-text">{row.friction}</p>
      <div className="ma-card-sub">
        {row.outcome && <span className="rf-tag is-accent">{OUTCOME_LABELS[row.outcome.outcome] || row.outcome.outcome}</span>}
        <span>{[when(row.at), PROVIDERS[row.call?.provider]].filter(Boolean).join(' · ')}</span>
      </div>
      {row.outcome?.note && <p className="rf-note">{row.outcome.note}</p>}
      {(chatId || run) && (
        <div className="rf-actions">
          {chatId && <button type="button" className="ma-btn ma-btn-ghost" onClick={() => openChat(chatId)}><Chat /> Where it happened</button>}
          {run && <button type="button" className="ma-btn ma-btn-ghost" onClick={() => onOpen(run)}><ChevronRight /> Report</button>}
        </div>
      )}
    </li>
  )
}

function Backlog({ runs, rows, runTime, onOpen }) {
  if (!rows.length) {
    return (
      <Empty title="No friction logged yet">
        Agents log friction here as it happens: a missing tool, a misleading
        error, an unclear instruction.
      </Empty>
    )
  }
  const { pending, decision, done, working } = backlogSections(rows, runs)
  const byId = new Map(runs.map((run) => [run.id, run]))
  const section = (title, list, empty) => (
    <section className="rf-section" aria-label={title}>
      <h2>{title} · {list.length}</h2>
      {list.length
        ? <ul className="rf-list">{list.map((row) => (
          <FrictionItem key={row.id || row.at} row={row} run={byId.get(row.outcome?.run)} onOpen={onOpen} />
        ))}</ul>
        : <p className="rf-quiet">{empty}</p>}
    </section>
  )
  return (
    <>
      {section(
        working ? 'Being worked on now' : 'Waiting for the next run',
        pending,
        runTime ? `Nothing waiting. The next run is at ${runTime}.` : 'Nothing waiting.',
      )}
      {section('Fixes proposed', decision, 'No fixes proposed.')}
      {section('Done', done, 'Nothing settled yet.')}
    </>
  )
}

function Settings({ appId, token, schedule, pendingCount, running, onChange }) {
  const [models, setModels] = useState([])
  const [agent, setAgent] = useState('')
  const [time, setTime] = useState(scheduleTime(schedule) || '06:00')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  // The time is shown and saved in the schedule's own zone; a schedule without
  // one takes this device's zone.
  const timezone = (schedule?.zone_cron && schedule.timezone)
    || Intl.DateTimeFormat().resolvedOptions().timeZone

  useEffect(() => {
    const store = window.mobius?.storage
    Promise.all([
      api(token, '/api/auth/providers/status').then((r) => (r.ok ? r.json() : {})).catch(() => ({})),
      api(token, '/api/auth/providers/models').then((r) => (r.ok ? r.json() : {})).catch(() => ({})),
      store?.get?.('settings.json').catch(() => null),
    ]).then(([connected, catalog, settings]) => {
      setModels(Object.keys(PROVIDERS)
        .filter((provider) => connected?.[provider]?.configured && Array.isArray(catalog?.[provider]))
        .map((provider) => ({ provider, models: catalog[provider].filter((m) => typeof m?.id === 'string') })))
      if (settings?.provider) setAgent(`${settings.provider}:${settings.model || ''}`)
    })
  }, [token])

  const save = async () => {
    setBusy(true)
    setStatus('Saving…')
    // Model ids may themselves contain a colon; only the first one separates.
    const cut = agent.indexOf(':')
    const [provider, model] = cut > 0 ? [agent.slice(0, cut), agent.slice(cut + 1)] : [null, null]
    try {
      await window.mobius.storage.set('settings.json', { provider: provider || null, model: model || null })
      const r = await api(token, `/api/apps/${appId}/schedule`, {
        method: 'POST', body: JSON.stringify({ cron: dailyCron(time), timezone }),
      })
      setStatus(r.ok ? 'Saved.' : 'The time could not be saved. Try again.')
      if (r.ok) onChange()
    } catch {
      setStatus('Could not save. Try again.')
    }
    setBusy(false)
  }

  const runNow = async () => {
    setBusy(true)
    const r = await api(token, `/api/apps/${appId}/run-job`, { method: 'POST' }).catch(() => null)
    setStatus(r?.ok ? 'Started. The run appears under Reports in a moment.' : 'Could not start a run.')
    setBusy(false)
    if (r?.ok) setTimeout(onChange, 4000)
  }

  return (
    <div className="rf-form">
      <label className="rf-label">
        Agent
        <span className="rf-hint">Who works through the friction. Automatic uses your background agents in order.</span>
        <select className="ma-input" value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="">Automatic (your background agents)</option>
          {models.map((group) => (
            <optgroup key={group.provider} label={PROVIDERS[group.provider]}>
              {group.models.map((m) => <option key={m.id} value={`${group.provider}:${m.id}`}>{m.name || m.id}</option>)}
            </optgroup>
          ))}
        </select>
      </label>
      <label className="rf-label">
        Daily run time
        <span className="rf-hint">When Reflection picks up new friction ({timezone}). Days with nothing new cost nothing.</span>
        <input className="ma-input" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
      </label>
      <div className="rf-row">
        <button type="button" className="ma-btn ma-btn-primary" onClick={save} disabled={busy}>Save</button>
        <button type="button" className="ma-btn ma-btn-secondary" onClick={runNow} disabled={busy || running || !pendingCount}>
          <Play /> {running ? 'Running' : `Run now${pendingCount ? ` (${pendingCount} waiting)` : ''}`}
        </button>
        <span className="rf-status" role="status">{status}</span>
      </div>
    </div>
  )
}

export default function App({ appId, token }) {
  const [tab, setTab] = useState('reports')
  const [openId, setOpenId] = useState(null)
  const [data, failed, reload, loadReport] = useData(appId, token)
  useEffect(() => { if (data || failed) window.mobius?.signal?.('app_ready') }, [data, failed])

  const open = data?.runs.find((run) => run.id === openId)
  let body
  if (!data) {
    body = failed
      ? (
        <div className="ma-empty">
          <div className="ma-empty-title">Couldn’t load Reflection</div>
          <p className="ma-empty-text">Check your connection and try again.</p>
          <button type="button" className="ma-btn ma-btn-secondary" onClick={reload}><Reload /> Try again</button>
        </div>
      )
      : <p className="rf-loading" role="status">Loading…</p>
  } else if (open) {
    body = <Report run={open} loadReport={loadReport} onBack={() => { setOpenId(null); reload() }} />
  } else {
    const pendingCount = data.rows.filter((row) => !row.outcome).length
    body = (
      <>
        <div className="ma-seg" role="tablist" aria-label="View">
          {TABS.map(([id, label]) => (
            <button key={id} type="button" role="tab" id={`rf-tab-${id}`} aria-controls="rf-panel"
              aria-selected={tab === id} className={`ma-seg-btn${tab === id ? ' is-active' : ''}`} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="rf-panel" aria-labelledby={`rf-tab-${tab}`}>
          {tab === 'reports' && <Reports runs={data.runs} rows={data.rows} onOpen={(run) => setOpenId(run.id)} />}
          {tab === 'backlog' && (
            <Backlog runs={data.runs} rows={data.rows} runTime={scheduleTime(data.schedule)} onOpen={(run) => setOpenId(run.id)} />
          )}
          {tab === 'settings' && (
            <Settings appId={appId} token={token} schedule={data.schedule} pendingCount={pendingCount}
              running={data.runs.some((run) => run.running)} onChange={reload} />
          )}
        </div>
      </>
    )
  }

  return (
    <div className="ma-root">
      <style>{CSS}</style>
      <Header appId={appId} status={data && statusLine(data)} />
      <main className="rf-page">{body}</main>
    </div>
  )
}
