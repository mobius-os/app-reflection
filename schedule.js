// The daily run time as the owner sees it, and the cron the platform stores.

// The platform returns either a zone-owned daily cron (with its timezone) or a
// plain cron in the server's timezone (UTC). Both become a local "HH:MM".
export function scheduleTime(schedule, now = new Date()) {
  const cron = schedule?.zone_cron || schedule?.cron || ''
  const match = /^(\d{1,2}) (\d{1,2}) \* \* \*$/.exec(cron.trim())
  if (!match) return null
  const [minute, hour] = [Number(match[1]), Number(match[2])]
  if (schedule.zone_cron) return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  const at = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hour, minute))
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
}

export function dailyCron(time) {
  const [hour, minute] = String(time).split(':').map(Number)
  return `${minute} ${hour} * * *`
}
