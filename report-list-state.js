export function reconcileReportDates(previous, listing) {
  const current = Array.isArray(previous) ? previous : []
  const incoming = Array.isArray(listing?.dates) ? listing.dates : null
  if (!incoming) return { dates: current, usable: current.length > 0 }
  if (listing.complete === true) return { dates: incoming, usable: true }
  if (incoming.length === 0) return { dates: current, usable: current.length > 0 }
  return {
    dates: [...new Set([...current, ...incoming])]
      .sort((a, b) => (a < b ? 1 : a > b ? -1 : 0)),
    usable: true,
  }
}
