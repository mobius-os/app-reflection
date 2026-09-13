import retiredModelIds from './retired-model-ids.json' with { type: 'json' }

// The browser and unattended runner consume the same packaged policy so a
// model retirement remains one app-owned edit.
export const RETIRED_MODEL_IDS = Object.freeze(retiredModelIds)

export function migrateAgentModels(settings) {
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return settings
  let migrated = settings
  for (const key of ['model', 'fallback_model']) {
    const replacement = RETIRED_MODEL_IDS[settings[key]]
    if (!replacement) continue
    if (migrated === settings) migrated = { ...settings }
    migrated[key] = replacement
  }
  return migrated
}
