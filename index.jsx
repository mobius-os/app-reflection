// Reflection — thin app shell. The module tree is declared in mobius.json's
// source_files; the multi-file installer fetches each path and Rolldown bundles
// from this entry, resolving the relative imports below at compile time.
//
//   constants.js  — shared scalar tables, report template blocks, and chat sizing constants
//   theme.js      — the single app stylesheet (CSS)
//   domain.js     — pure + DOM-level report, schedule, date, and split helpers
//   providers.js  — provider/model API loading helpers
//   storage.js    — storage layer, online signal, and chat split persistence keys
//   ui/*.jsx      — one React component per file
//
// Only App lives here: it owns top-level tab/detail state, persistence wiring,
// app-ready/dead-letter signals, and mounts the report/settings UI.
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { X } from '@openai/apps-sdk-ui/components/Icon'
import { CSS } from './theme.js'
import { makeStorage, useOnline } from './storage.js'
import { ReportDetail } from './ui/ReportDetail.jsx'
import { ReportsList } from './ui/ReportsList.jsx'
import { SettingsTab } from './ui/SettingsTab.jsx'

export {
  extractReportQuestions,
  hardenReportHtml,
  isDarkColor,
  reportThemeStyle,
  sanitizeQuestions,
} from './domain.js'
export { makeStorage } from './storage.js'

const SETUP_COMPLETIONS_KEY = 'mobius:setup-complete:v1'

function markSetupComplete(appId) {
  if (appId == null || typeof window === 'undefined') return
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SETUP_COMPLETIONS_KEY) || '{}')
    const data = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
    data[String(appId)] = { completedAt: new Date().toISOString() }
    window.localStorage.setItem(SETUP_COMPLETIONS_KEY, JSON.stringify(data))
  } catch {}
  if (window.parent && window.parent !== window) {
    window.parent.postMessage(
      { type: 'moebius:setup-complete', appId },
      window.location.origin,
    )
  }
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App({ appId, token }) {
  const [tab, setTab] = useState('reports')
  const [openDate, setOpenDate] = useState(null)
  const detailNavRef = useRef(null)
  const tabRefs = useRef([])
  const online = useOnline()
  const storage = useMemo(() => makeStorage(appId, token), [appId, token])
  const selectTab = (next) => {
    if (next === 'settings') closeDetail()
    setTab(next)
  }
  const onTabKeyDown = (event, index) => {
    const order = ['reports', 'settings']
    let nextIndex = index
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % order.length
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + order.length) % order.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = order.length - 1
    else return
    event.preventDefault()
    selectTab(order[nextIndex])
    window.requestAnimationFrame(() => tabRefs.current[nextIndex]?.focus())
  }
  const appReadyFiredRef = useRef(false)
  // A save can resolve 'queued' (durably outboxed offline) and then be FATALLY
  // refused later, when the outbox drains — an async outcome the resolved
  // promise at the call site can never carry. onDeadLetter is that out-of-band
  // channel: it fires once per such write so a "Saved" the user already saw is
  // honestly retracted here. Held at the app root because the originating
  // component (a question card, the settings form) is likely unmounted by drain
  // time. Replays unconsumed dead-letters on subscribe, so a refusal that
  // landed while the app was closed still surfaces on next open.
  const [deadLetter, setDeadLetter] = useState(null)
  useEffect(() => {
    if (!window.mobius || typeof window.mobius.onDeadLetter !== 'function') return undefined
    return window.mobius.onDeadLetter((dl) => {
      setDeadLetter(dl && dl.path === 'settings.json'
        ? 'Your schedule didn’t save — it was refused after going offline. Reopen Settings and save again.'
        : 'A queued change couldn’t be saved after you reconnected. Please try again.')
    })
  }, [])

  // Surface the streak in the header on the reports tab. The read below goes
  // through the runtime read-through cache (offline-capable), so the badge
  // fills from the last-known state.json even before the list finishes its own
  // load — and offline too. The list keeps its own authoritative copy.
  const [headerStreak, setHeaderStreak] = useState(0)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const res = await storage.getJSON('state.json')
      if (cancelled) return
      if (res.data && Number.isFinite(res.data.streak)) {
        setHeaderStreak(res.data.streak)
      }
      // app_ready fires once after the initial state load (whether empty or not).
      if (!appReadyFiredRef.current) {
        appReadyFiredRef.current = true
        window.mobius?.signal?.('app_ready')
      }
    })()
    return () => { cancelled = true }
  }, [storage, appId, token])

  const closeDetail = useCallback(() => {
    try { detailNavRef.current?.close?.() } catch {}
    detailNavRef.current = null
    setOpenDate(null)
  }, [])

  useEffect(() => {
    function onMessage(e) {
      if (e.origin !== window.location.origin) return
      if (e.data?.type === 'moebius:app-intent' && e.data.intent === 'setup') {
        closeDetail()
        setTab('settings')
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [closeDetail])

  const openDetail = useCallback(async (dateStr) => {
    try { detailNavRef.current?.close?.() } catch {}
    detailNavRef.current = null
    if (window.mobius?.nav?.open) {
      let handle = null
      handle = window.mobius.nav.open('reflection-report', {
        onBack: () => {
          if (detailNavRef.current !== handle) return
          detailNavRef.current = null
          setOpenDate(null)
        },
        onForward: () => {
          detailNavRef.current = handle
          setOpenDate(dateStr)
        },
      })
      detailNavRef.current = handle
      const { status } = await handle.outcome
      if (detailNavRef.current !== handle) {
        handle.close()
        return
      }
      if (status !== 'owned' && status !== 'standalone') {
        detailNavRef.current = null
        return
      }
    }
    window.mobius?.signal?.('brief_opened', { date: dateStr })
    setOpenDate(dateStr)
  }, [])

  useEffect(() => () => {
    try { detailNavRef.current?.close?.() } catch {}
  }, [])

  return (
    <div className="rf-root">
      <style>{CSS}</style>
      <div className="rf-aurora" aria-hidden="true" />
      <div className="rf-header">
        <div className="rf-brand">
          {/* Brand mark: the app's real glossy icon (downscaled + cached).
              Falls back to an accent tile when this install
              has no custom icon and the route 404s. */}
          <img
            src={`/api/apps/${appId}/icon?size=64`}
            alt=""
            width={26}
            height={26}
            className="rf-brand-icon"
            onError={(e) => {
              e.currentTarget.style.display = 'none'
              const f = e.currentTarget.nextElementSibling
              if (f) f.style.display = 'flex'
            }}
          />
          <span className="rf-brand-fallback" style={{ display: 'none' }} aria-hidden="true">R</span>
          <div className="rf-brand-copy">
            <h1>Reflection</h1>
            <span>Daily briefs from your agent</span>
          </div>
        </div>
        <div className="rf-header-right">
          {headerStreak >= 1 && (
            <span className="rf-streak-badge" title={`${headerStreak} mornings in a row`}>
              <span aria-hidden="true">🔥</span>
              {headerStreak}
            </span>
          )}
          <div className="rf-seg" role="tablist" aria-label="View">
            <button
              id="rf-tab-reports"
              ref={(node) => { tabRefs.current[0] = node }}
              type="button"
              role="tab"
              aria-selected={tab === 'reports'}
              aria-controls="rf-panel-reports"
              tabIndex={tab === 'reports' ? 0 : -1}
              className={`rf-seg-btn${tab === 'reports' ? ' is-active' : ''}`}
              onClick={() => selectTab('reports')}
              onKeyDown={(event) => onTabKeyDown(event, 0)}
            >
              Briefs
            </button>
            <button
              id="rf-tab-settings"
              ref={(node) => { tabRefs.current[1] = node }}
              type="button"
              role="tab"
              aria-selected={tab === 'settings'}
              aria-controls="rf-panel-settings"
              tabIndex={tab === 'settings' ? 0 : -1}
              className={`rf-seg-btn${tab === 'settings' ? ' is-active' : ''}`}
              onClick={() => selectTab('settings')}
              onKeyDown={(event) => onTabKeyDown(event, 1)}
            >
              Settings
            </button>
          </div>
        </div>
      </div>
      <div className="rf-divider" />
      <div className="rf-scroll">
        {deadLetter && (
          <div className="rf-deadletter" role="alert">
            <span>{deadLetter}</span>
            <button
              type="button"
              className="rf-deadletter__x rf-pressable"
              aria-label="Dismiss"
              onClick={() => setDeadLetter(null)}
            >
              <X width="1em" height="1em" aria-hidden="true" />
            </button>
          </div>
        )}
        {tab === 'reports' ? (
          <div id="rf-panel-reports" role="tabpanel" aria-labelledby="rf-tab-reports">
            <ReportsList
              appId={appId}
              storage={storage}
              online={online}
              onOpen={openDetail}
              onSetup={() => { closeDetail(); setTab('settings') }}
            />
            {openDate && (
              <ReportDetail
                dateStr={openDate}
                storage={storage}
                online={online}
                onBack={closeDetail}
                appId={appId}
                token={token}
              />
            )}
          </div>
        ) : (
          <div id="rf-panel-settings" role="tabpanel" aria-labelledby="rf-tab-settings">
            <SettingsTab
              appId={appId}
              storage={storage}
              token={token}
              onSetupComplete={() => markSetupComplete(appId)}
            />
          </div>
        )}
      </div>
    </div>
  )
}
