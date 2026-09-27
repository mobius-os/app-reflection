#!/usr/bin/env bash
# Reflection's daily job. When logged friction still lacks an outcome, open
# a background Reflection chat (reached from the app, not the chat list) and
# hand it the backlog; otherwise do nothing.
# The chat is an ordinary Möbius agent turn: the platform supervises it, picks
# the model from the owner's background agents, and records its cost. Every run
# with pending friction gets its own chat. Should two runs overlap, a duplicate
# outcome is harmless: outcomes are keyed by friction id.
set -euo pipefail

APP_ID="${1:?numeric app id required}"
STORAGE="${DATA_DIR:-/data}/apps/$APP_ID"
TODAY="$(date +%F)"
RUN="$(date -u +%Y%m%dT%H%M%S)"

PENDING="$(python3 "$(dirname "$0")/friction_queue.py" pending "$STORAGE" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
[ "$PENDING" -gt 0 ] || exit 0

python3 - "$TODAY" "$RUN" "$PENDING" "$STORAGE/settings.json" <<'PY' | curl -sf -X POST \
  -H "Authorization: Bearer $APP_TOKEN" -H "Content-Type: application/json" \
  --data-binary @- "${API_BASE_URL:-http://localhost:8000}/api/app-chats/start" >/dev/null
import json, sys
today, run, pending, settings_path = sys.argv[1:]
try:
  settings = json.load(open(settings_path, encoding="utf-8"))
except (OSError, ValueError):
  settings = {}
# The owner's chosen agent; without one the platform uses their background
# agents list.
agent = {key: settings[key] for key in ("provider", "model") if settings.get(key)}
print(json.dumps({
  **agent,
  "scope": f"run-{run}",
  "title": f"Reflection — {today}",
  "content": (
    f"Friction entries waiting for an outcome: {pending}. Work through them "
    "following the `reflection` skill."
  ),
}))
PY
