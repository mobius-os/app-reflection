import { useCallback, useEffect, useState } from 'react'
import {
  DEFAULT_CRON,
  DEFAULT_HOUR,
  DEFAULT_MODEL,
  DEFAULT_PROVIDER,
  EFFORT_LEVELS,
  FALLBACK_MODEL_GROUPS,
  defaultEffort,
} from '../constants.js'
import { buildCron, hourClockLabel, hourToTimeValue, parseCronHour } from '../domain.js'
import { fetchModelConfig } from '../providers.js'
import { EffortStepper } from './EffortStepper.jsx'
import { ModelPicker } from './ModelPicker.jsx'
import { BackgroundAgentList } from './BackgroundAgentList.jsx'
import { agentSlotLabel, canReorderAgentSlots, reorderAgentSlots } from './backgroundAgentOrder.js'

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function effortForProvider(provider, value) {
  const levels = EFFORT_LEVELS[provider] || []
  return levels.some((level) => level.value === value) ? value : defaultEffort(provider)
}

function effortLabel(provider, value) {
  const levels = EFFORT_LEVELS[provider] || []
  return levels.find((level) => level.value === effortForProvider(provider, value))?.label || ''
}

function withoutLegacyBriefControls(settings) {
  const { verbosity, focus, avoid, ...rest } = settings || {}
  return rest
}

export function SettingsTab({ appId, storage, token, onSetupComplete }) {
  const [hour, setHour] = useState(DEFAULT_HOUR)
  const [excludeApps, setExcludeApps] = useState([])
  const [settingsExtra, setSettingsExtra] = useState({})
  const [useSystemPrimary, setUseSystemPrimary] = useState(true)
  const [provider, setProvider] = useState(DEFAULT_PROVIDER)
  const [model, setModel] = useState(DEFAULT_MODEL)
  const [effort, setEffort] = useState(defaultEffort(DEFAULT_PROVIDER))
  const [useSystemSecondary, setUseSystemSecondary] = useState(true)
  const [fallbackProvider, setFallbackProvider] = useState('')
  const [fallbackModel, setFallbackModel] = useState('')
  const [fallbackEffort, setFallbackEffort] = useState('')
  const [modelGroups, setModelGroups] = useState(null)
  const [connectedProviders, setConnectedProviders] = useState(null)
  // The raw cron we loaded — when it's a custom shape parseCronHour can't
  // represent (a non-zero minute, multiple hours), we surface it read-only
  // rather than silently rewriting it to "0 <h> * * *" on the next save.
  const [rawCron, setRawCron] = useState(DEFAULT_CRON)
  const [cronIsCustom, setCronIsCustom] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const res = await storage.getJSON('settings.json')
      if (cancelled) return
      const s = res.data && typeof res.data === 'object' ? res.data : null
      if (s) {
        setSettingsExtra(withoutLegacyBriefControls(s))
        const parsedHour = parseCronHour(s.cron)
        if (parsedHour != null) {
          setHour(parsedHour)
          setCronIsCustom(false)
        } else if (typeof s.cron === 'string' && s.cron.trim()) {
          // Hand-edited / multi-hour cron — keep it, show it read-only.
          setRawCron(s.cron)
          setCronIsCustom(true)
        } else if (Number.isFinite(s.hour) && s.hour >= 0 && s.hour <= 23) {
          // Legacy seed shape used hour/minute/timezone. Preserve it as a
          // readable default, then save in the cron shape the runner expects.
          setHour(s.hour)
          setCronIsCustom(false)
        }
        if (Array.isArray(s.exclude_apps)) setExcludeApps(s.exclude_apps)
        const primaryMode = s.primary_agent_mode
        const providerValue = typeof s.provider === 'string' ? s.provider.trim() : ''
        const modelValue = typeof s.model === 'string' ? s.model.trim() : ''
        const effortValue = typeof s.effort === 'string' ? s.effort.trim() : ''
        const legacyDefaultPrimary = (
          !primaryMode &&
          providerValue === DEFAULT_PROVIDER &&
          !modelValue &&
          !effortValue
        )
        const hasPrimaryOverride = primaryMode === 'app' || primaryMode === 'custom' || (
          primaryMode !== 'system' &&
          !legacyDefaultPrimary &&
          Boolean(providerValue || modelValue || effortValue)
        )
        setUseSystemPrimary(!hasPrimaryOverride)
        const secondaryMode = s.secondary_agent_mode
        const hasSecondaryOverride = secondaryMode === 'app' || secondaryMode === 'custom' || (
          secondaryMode !== 'system' &&
          Boolean(s.fallback_provider || s.fallback_model || s.fallback_effort)
        )
        setUseSystemSecondary(!hasSecondaryOverride)
        if (typeof s.provider === 'string' && s.provider.trim()) {
          setProvider(s.provider.trim())
        }
        if (typeof s.model === 'string' && s.model.trim()) {
          setModel(s.model.trim())
        }
        setEffort(effortForProvider(providerValue || DEFAULT_PROVIDER, effortValue))
        if (typeof s.fallback_provider === 'string' && s.fallback_provider.trim()) {
          setFallbackProvider(s.fallback_provider.trim())
        }
        if (typeof s.fallback_model === 'string' && s.fallback_model.trim()) {
          setFallbackModel(s.fallback_model.trim())
        }
        const fallbackProviderValue = typeof s.fallback_provider === 'string' ? s.fallback_provider.trim() : ''
        const fallbackEffortValue = typeof s.fallback_effort === 'string' ? s.fallback_effort.trim() : ''
        if (fallbackProviderValue) {
          setFallbackEffort(effortForProvider(fallbackProviderValue, fallbackEffortValue))
        }
      }
      // res.notFound (first run) -> keep the 06:00 defaults.
      setLoading(false)
    })()
    return () => { cancelled = true }
  }, [storage])

  useEffect(() => {
    let cancelled = false
    fetchModelConfig(token)
      .then(({ connected, models }) => {
        if (cancelled) return
        setConnectedProviders(connected)
        setModelGroups(models)
      })
      .catch(() => {
        if (cancelled) return
        setModelGroups(FALLBACK_MODEL_GROUPS)
      })
    return () => { cancelled = true }
  }, [token])

  const onTimeChange = useCallback((e) => {
    // <input type="time"> can be cleared to "" -> NaN. Drop NaN so we never
    // write a corrupt cron; the input repaints with the last good value.
    const [hStr] = e.target.value.split(':')
    const h = Number(hStr)
    if (Number.isFinite(h) && h >= 0 && h <= 23) {
      setHour(h)
      setCronIsCustom(false) // editing the hour adopts the standard shape
    }
  }, [])

  const save = useCallback(async () => {
    if (saving) return
    setSaving(true)
    setError('')
    setToast('')
    // Preserve a custom cron verbatim if the user never touched the hour;
    // otherwise write the standard "0 <h> * * *".
    const cron = cronIsCustom ? rawCron : buildCron(hour)
    try {
      // durableWrite resolves on a durable outcome — 'synced' (server accepted)
      // or 'queued' (outboxed offline, guaranteed retry). Both are genuinely
      // saved, so either flips the picker to "Saved ✓": a queued schedule WILL
      // reach the server, and if the queue ever fatally fails on drain,
      // onDeadLetter (wired on App mount) surfaces that asynchronously. Only a
      // fatal server refusal (413/400/403) rejects, dropping into catch below.
      await storage.putJSON('settings.json', {
        ...settingsExtra,
        cron,
        hour,
        minute: 0,
        timezone: settingsExtra.timezone ?? null,
        exclude_apps: excludeApps,
        provider: useSystemPrimary ? null : (provider || settingsExtra.provider || DEFAULT_PROVIDER),
        model: useSystemPrimary ? null : (model || settingsExtra.model || null),
        effort: useSystemPrimary ? null : effortForProvider(provider || DEFAULT_PROVIDER, effort),
        fallback_provider: !useSystemSecondary ? (fallbackProvider || null) : null,
        fallback_model: !useSystemSecondary && fallbackProvider ? (fallbackModel || null) : null,
        fallback_effort: !useSystemSecondary && fallbackProvider
          ? effortForProvider(fallbackProvider, fallbackEffort)
          : null,
        primary_agent_mode: useSystemPrimary ? 'system' : 'app',
        secondary_agent_mode: useSystemSecondary ? 'system' : 'app',
      })
      // Launch analytics: which durable settings the owner customized.
      window.mobius?.signal?.('settings_saved', {
        hour,
        custom_cron: cronIsCustom,
        use_system_primary: useSystemPrimary,
        has_fallback: Boolean(fallbackProvider),
        exclude_count: excludeApps.length,
      })
      onSetupComplete?.()
      setToast('Saved ✓')
      setTimeout(() => setToast(''), 2600)
    } catch {
      // A fatal DurableWriteError (the server refused the write) — never a mere
      // outage, which would have resolved 'queued'. Surface a plain save error.
      setError('Could not save — try again.')
    } finally {
      setSaving(false)
    }
  }, [saving, cronIsCustom, rawCron, hour, excludeApps, useSystemPrimary, provider, model, effort, useSystemSecondary, fallbackProvider, fallbackModel, fallbackEffort, settingsExtra, storage, onSetupComplete])

  const reorderAgents = useCallback((fromIndex, toIndex) => {
    const slots = [{
      mode: useSystemPrimary ? 'system' : 'app',
      provider,
      model,
      effort,
    }, {
      mode: useSystemSecondary ? 'system' : 'app',
      provider: fallbackProvider,
      model: fallbackModel,
      effort: fallbackEffort,
    }]
    const ordered = reorderAgentSlots(slots, fromIndex, toIndex)
    if (ordered === slots) return false
    const [primary, secondary] = ordered
    setUseSystemPrimary(primary.mode === 'system')
    setProvider(primary.provider)
    setModel(primary.model)
    setEffort(primary.effort)
    setUseSystemSecondary(secondary.mode === 'system')
    setFallbackProvider(secondary.provider)
    setFallbackModel(secondary.model)
    setFallbackEffort(secondary.effort)
    return true
  }, [useSystemPrimary, provider, model, effort, useSystemSecondary, fallbackProvider, fallbackModel, fallbackEffort])

  const agentSlots = [{
    mode: useSystemPrimary ? 'system' : 'app', provider, model, effort,
  }, {
    mode: useSystemSecondary ? 'system' : 'app',
    provider: fallbackProvider, model: fallbackModel, effort: fallbackEffort,
  }]
  const canReorderAgents = canReorderAgentSlots(agentSlots)
  const agentLabels = [
    agentSlotLabel(agentSlots[0], modelGroups, 'Settings default primary agent'),
    agentSlotLabel(agentSlots[1], modelGroups, 'Settings default secondary agent'),
  ]

  if (loading) {
    return (
      <div className="rf-loading-wrap">
        <span className="rf-spinner" aria-hidden="true" />
        <div>Loading settings…</div>
      </div>
    )
  }

  return (
    <div className="rf-settings-wrap rf-rise">
      <div className="rf-settings-card">
        <div className="rf-section-head">
          <span className="rf-section-icon" aria-hidden="true">⏰</span>
          <h2 className="rf-section-label">When it runs</h2>
        </div>
        <p className="rf-note">
          Pick the hour your morning brief should be ready. Reflection writes it
          overnight so it’s waiting when you wake.
        </p>
        {cronIsCustom ? (
          <div className="rf-custom-cron-note">
            You have a custom schedule set (<code>{rawCron}</code>). Pick an
            hour below to switch to a simple daily time, or leave it as-is.
            <div className="rf-time-row">
              <input
                type="time"
                step="3600"
                className="rf-time-input"
                value={hourToTimeValue(hour)}
                onChange={onTimeChange}
                aria-label="Daily brief time"
              />
              <span className="rf-note">on the hour, every day</span>
            </div>
          </div>
        ) : (
          <div className="rf-time-row">
            <input
              type="time"
              step="3600"
              className="rf-time-input"
              value={hourToTimeValue(hour)}
              onChange={onTimeChange}
              aria-label="Daily brief time"
            />
            <span className="rf-note">
              ready around <strong className="rf-note-strong">{hourClockLabel(hour)}</strong>, every day
            </span>
          </div>
        )}
        <div className="rf-schedule-hint">
          <span aria-hidden="true">💡</span>
          <span>
            Schedule changes take effect after the reflection agent re-installs
            its overnight job — usually by the next run. The app saves your
            preference; the agent picks it up from there.
          </span>
        </div>
      </div>

      <div className="rf-settings-card">
        <div className="rf-section-head">
          <span className="rf-section-icon" aria-hidden="true">🤖</span>
          <h2 className="rf-section-label">Background agents</h2>
        </div>
        <p className="rf-note">
          Tried in order. Drag to change priority. Each row follows Möbius
          Settings by default, or can use its own model for Reflection.
        </p>
        {modelGroups === null ? (
          <div className="rf-note">Loading models…</div>
        ) : modelGroups.length === 0 ? (
          // Models API unavailable — fall back to letting the CLI choose.
          <div className="rf-note">
            Model list unavailable. Reflection will use the CLI's default model
            for your account.
          </div>
        ) : (
          <BackgroundAgentList
            onMove={reorderAgents}
            itemLabels={agentLabels}
            reorderDisabled={!canReorderAgents}
            reorderDisabledReason="Choose an app override for both rows before changing priority; inherited Settings agents keep their Möbius Settings order."
          >
            <div key="primary">
              <ModelPicker
                provider={useSystemPrimary ? '' : provider}
                model={useSystemPrimary ? '' : model}
                groups={modelGroups}
                connectedProviders={connectedProviders}
                title="Reflection primary model"
                navKey="reflection-primary-model"
                useSettingsDefault={useSystemPrimary}
                onSettingsDefault={() => setUseSystemPrimary(true)}
                effortLabel={useSystemPrimary ? '' : effortLabel(provider, effort)}
                efforts={EFFORT_LEVELS[provider] || []}
                effort={effort}
                effortControl={useSystemPrimary ? null : (
                  <EffortStepper provider={provider} value={effort} onChange={setEffort} />
                )}
                onChange={(nextProvider, nextModel) => {
                  setUseSystemPrimary(false)
                  setProvider(nextProvider)
                  setModel(nextModel || null)
                  setEffort(effortForProvider(nextProvider, effort))
                }}
              />
            </div>
            <div key="secondary">
              <ModelPicker
                provider={useSystemSecondary ? '' : fallbackProvider}
                model={useSystemSecondary ? '' : fallbackModel}
                groups={modelGroups}
                connectedProviders={connectedProviders}
                title="Reflection secondary model"
                navKey="reflection-secondary-model"
                useSettingsDefault={useSystemSecondary}
                onSettingsDefault={() => setUseSystemSecondary(true)}
                effortLabel={useSystemSecondary ? '' : effortLabel(fallbackProvider, fallbackEffort)}
                efforts={EFFORT_LEVELS[fallbackProvider] || []}
                effort={fallbackEffort}
                effortControl={useSystemSecondary ? null : (
                  <EffortStepper
                    provider={fallbackProvider}
                    value={fallbackEffort}
                    onChange={setFallbackEffort}
                  />
                )}
                onChange={(nextProvider, nextModel) => {
                  setUseSystemSecondary(false)
                  setFallbackProvider(nextProvider)
                  setFallbackModel(nextModel || null)
                  setFallbackEffort(effortForProvider(nextProvider, fallbackEffort))
                }}
              />
            </div>
          </BackgroundAgentList>
        )}
      </div>

      <div className="rf-save-row">
        <button className="rf-save-btn rf-pressable" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save settings'}
        </button>
        {toast && <span className="rf-toast">{toast}</span>}
        {error && <span className="rf-error-toast">{error}</span>}
      </div>
    </div>
  )
}
