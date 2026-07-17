#!/bin/bash
# fetch.sh — the nightly "reflection" wrapper. Thin by design: it owns
# only the OPERATIONAL concerns of an unattended cron run — no overlap,
# a wall-clock timeout, liveness heartbeats, an outcome event — and then
# hands the night to the agent.
#
# Unlike v1, this wrapper is NOT a security boundary. The Reflection agent
# runs with FULL tools and a REAL token (no staging tree, no
# Bash-less/token-less envelope, no wrapper-owned validation gate). It forks
# chats, reviews Memory's update log, edits skills, fixes apps, writes
# the brief to reports/<date>.html via the storage API, and commits —
# all itself, instructed by its skill
# (/data/shared/skills/reflection.md), per Möbius's "code empowers the
# agent; it does not police it." Reversibility comes from git, not from
# walls. So this file gathers a little read-only context for the agent,
# exports the few env vars its shell needs, runs the runner under a lock
# + timeout, and records how the night finished.
#
# Invoked by cron as: /data/apps/reflection/fetch.sh <app_id>
# (the app id arrives as $1, per the cron-scaffold convention).
#
# REFLECTION_DRY=1 skips the real agent run (records a dry outcome) so the
# plumbing — lock, inputs, env, heartbeat, cron_outcome — can be smoke-
# tested without spending a nightly run.
set -uo pipefail

APP_ID="${1:-}"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATA_DIR="${DATA_DIR:-/data}"
LOG="$DATA_DIR/cron-logs/reflection.log"
LOCK="$DATA_DIR/cron-logs/reflection.lock"
HEARTBEAT="$DATA_DIR/cron-logs/reflection.heartbeat"
TOKEN_FILE="$DATA_DIR/service-token.txt"
DATE="$(date +%F)"
INPUTS="$DATA_DIR/apps/reflection/inputs"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
RUNNER="${REFLECTION_RUNNER:-$SCRIPT_DIR/reflection_runner.py}"
# Wall-clock cap for the whole night. Generous (the agent does real,
# multi-phase work) but bounded so a wedged run can't hold the lock past
# the next night's schedule. Overridable for tests.
RUN_TIMEOUT="${REFLECTION_TIMEOUT:-7200}"
RUN_METRICS="$DATA_DIR/apps/reflection/reflection-run-metrics.jsonl"
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
RUN_STARTED_EPOCH="$(date +%s)"
RUN_DISK_BEFORE="$(python3 - "$DATA_DIR" <<'PY' 2>/dev/null || echo 0
import shutil, sys
print(shutil.disk_usage(sys.argv[1]).used)
PY
)"
RUN_CPU_BEFORE="$(awk '$1 == "usage_usec" {print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null || echo 0)"

# CLI credentials the spawned claude/codex binary reads. Exported (not
# just set) so the runner and any subprocess it forks inherit them.
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$DATA_DIR/cli-auth/claude}"
export CODEX_HOME="${CODEX_HOME:-$DATA_DIR/cli-auth/codex}"
export API_BASE_URL DATA_DIR

mkdir -p "$DATA_DIR/cron-logs" "$INPUTS"
log() { echo "[$(date -Iseconds)] reflection: $*" >>"$LOG"; }

# emit_outcome <exit_code> — one cron_outcome activity event recording
# how the night finished, so the next night's agent (and the Reflection
# app) can see the run history. Routed through the API so one process
# owns the activity-log file handle. Defined early because the token
# guard below emits a failure outcome before the main run.
emit_outcome() {
  local exit_code="$1"
  [[ -r "$TOKEN_FILE" ]] || return 0
  local token ts payload
  token="$(cat "$TOKEN_FILE")"
  ts="$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")"
  payload="$(printf '{"ev":"cron_outcome","ts":"%s","app_id":%s,"job":"reflection","exit_code":%s}' \
    "$ts" "${APP_ID:-0}" "$exit_code")"
  # The activity log is the PRIMARY liveness signal the next night's run
  # reads, so a dropped emit is invisible there (only this .log file keeps
  # it). Retry a transient API blip (restart/overload) with backoff before
  # giving up.
  local attempt=0
  while (( attempt < 3 )); do
    if curl -fsS -X POST \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "$API_BASE_URL/api/admin/activity/emit" >/dev/null 2>>"$LOG"; then
      return 0
    fi
    attempt=$(( attempt + 1 ))
    (( attempt < 3 )) && { log "WARN cron_outcome emit attempt $attempt failed; retrying"; sleep $(( 2 ** attempt )); }
  done
  log "WARN cron_outcome emit failed after 3 attempts (rc=$exit_code); NOT recorded in activity log"
  return 1
}

# --- no-overlap lock (flock) ------------------------------------------
# fd 9 holds the lock for the life of this process; flock -n fails fast
# if a prior night is still running (a long run that overran its window).
# Exit-code legend (recorded as the cron_outcome exit_code, so the next
# run + the Reflection app can tell a real success from a no-op):
#   0  success           3  service token missing
#   2  app id missing    5  skipped (a prior run still holds the lock)
#   124 wall-clock timeout    other  agent run error
exec 9>"$LOCK"
if ! flock -n 9; then
  log "another reflection run holds the lock; skipping this night (exit 5)"
  emit_outcome 5
  exit 5
fi

# --- token: export for the agent's shell (NOT a boundary) -------------
# The agent does its own privileged work (API reads, storage writes,
# notifications, git) using this token. We export it; we do NOT mediate
# the agent's use of it. A missing token means the agent can't reach the
# API, so fail loud rather than run a crippled night.
if [[ ! -r "$TOKEN_FILE" ]]; then
  log "ERROR service token unreadable ($TOKEN_FILE) — is the instance signed out? exiting"
  emit_outcome 3
  exit 3
fi
SERVICE_TOKEN="$(cat "$TOKEN_FILE")"
# Both names: AGENT_TOKEN is what the skill's curl examples use; the
# wrapper-era scripts read SERVICE_TOKEN. Export both so either works.
export SERVICE_TOKEN AGENT_TOKEN="$SERVICE_TOKEN"
auth=(-H "Authorization: Bearer $SERVICE_TOKEN")

# App id ($1) scopes storage + the cron_outcome. Checked AFTER the token
# block (not before, as it was) so a missing id is still recorded in the
# activity log — emit_outcome needs the token, so an earlier exit was
# invisible there, asymmetric with the token-missing path above.
if [[ -z "$APP_ID" ]]; then
  log "ERROR no app id passed as \$1; exiting"
  emit_outcome 2
  exit 2
fi

log "start (app_id=$APP_ID date=$DATE dry=${REFLECTION_DRY:-0} timeout=${RUN_TIMEOUT}s)"

# --- gather read-only inputs for the agent ----------------------------
# The agent reads these from inputs/ as its starting context. It can (and
# does) gather more itself with its token — these are just the obvious
# 24h slices so it doesn't spend its first turns on boilerplate API
# calls. Best-effort inputs carry explicit source status: a failed gather must
# not masquerade as a genuine empty observation window.

# activity.jsonl — last 24h of platform events (app opens, storage
# writes, cron_outcomes). The runner's goal message points the agent here.
SINCE="$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
ACTIVITY_STATUS="$INPUTS/activity-status.json"

write_activity_status() {
  local ok="$1" error="$2" event_count="${3:-}" sha256="${4:-}"
  python3 - "$ACTIVITY_STATUS" "$ok" "$error" "$event_count" "$SINCE" "$sha256" <<'PY'
import datetime, json, os, pathlib, sys

target = pathlib.Path(sys.argv[1])
ok = sys.argv[2] == "true"
error = sys.argv[3]
event_count = sys.argv[4]
payload = {
    "ok": ok,
    "since": sys.argv[5],
    "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
if event_count:
    payload["event_count"] = int(event_count)
if sys.argv[6]:
    payload["sha256"] = sys.argv[6]
if error:
    payload["error"] = error[:500]
if not ok:
    payload["retained_previous_snapshot"] = (target.parent / "activity.jsonl").exists()
tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
with tmp.open("w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    f.write("\n")
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, target)
PY
}

record_activity_status() {
  if ! write_activity_status "$@" 2>>"$LOG"; then
    # Never leave yesterday's `ok:true` sidecar beside a failed current fetch.
    # Missing status makes the digest fail closed with an explicit source error.
    rm -f -- "$ACTIVITY_STATUS"
    log "WARN could not persist activity source status"
  fi
}

# Never stream directly over the last good snapshot. curl -f rejects HTTP
# errors, its non-zero exit catches interrupted transfers, validation rejects a
# syntactically successful non-NDJSON body, and same-directory rename installs
# the new source atomically only after all three checks pass.
# Fail closed before touching the snapshot. If the process dies between the
# snapshot rename and its success sidecar, the digest sees this in-progress
# marker rather than pairing new bytes with yesterday's ok:true status.
record_activity_status false "activity fetch in progress" ""
ACTIVITY_TMP="$(mktemp "$INPUTS/.activity.jsonl.XXXXXX" 2>>"$LOG" || true)"
if [[ -z "$ACTIVITY_TMP" ]]; then
  log "WARN activity gather could not create a temporary file"
  record_activity_status false "could not create activity download temporary file" ""
elif curl -fsS --connect-timeout 10 --max-time 60 "${auth[@]}" \
    "$API_BASE_URL/api/admin/activity?since=$SINCE" \
    >"$ACTIVITY_TMP" 2>>"$LOG"; then
  if ACTIVITY_EVENT_COUNT="$(python3 - "$ACTIVITY_TMP" <<'PY' 2>>"$LOG"
import json, sys

count = 0
with open(sys.argv[1], encoding="utf-8") as f:
    for line_no, line in enumerate(f, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"activity NDJSON line {line_no} is invalid: {exc}")
        if not isinstance(event, dict) or not isinstance(event.get("ev"), str) \
                or not isinstance(event.get("ts"), str):
            raise SystemExit(
                f"activity NDJSON line {line_no} lacks string ev/ts fields"
            )
        count += 1
print(count)
PY
  )"; then
    if mv -f -- "$ACTIVITY_TMP" "$INPUTS/activity.jsonl"; then
      ACTIVITY_TMP=""
      ACTIVITY_SHA256="$(sha256sum "$INPUTS/activity.jsonl" | awk '{print $1}')"
      record_activity_status true "" "$ACTIVITY_EVENT_COUNT" "$ACTIVITY_SHA256"
    else
      log "WARN activity gather could not atomically install its snapshot"
      record_activity_status false "could not install validated activity snapshot" ""
    fi
  else
    log "WARN activity gather returned invalid NDJSON"
    record_activity_status false "activity response was not valid NDJSON" ""
  fi
else
  activity_curl_rc="$?"
  log "WARN activity gather failed (curl rc=$activity_curl_rc); retaining prior snapshot"
  record_activity_status false "activity fetch failed (curl exit $activity_curl_rc)" ""
fi
[[ -z "${ACTIVITY_TMP:-}" ]] || rm -f -- "$ACTIVITY_TMP"

# chats.md — recent chats list (titles + ids + provider), so the agent
# knows which sessions to fork-and-interview without re-deriving the list.
python3 - "$API_BASE_URL" "$SERVICE_TOKEN" >"$INPUTS/chats.md" 2>>"$LOG" <<'PY' || true
import json, sys, urllib.request
base, token = sys.argv[1], sys.argv[2]
def get(path):
    req = urllib.request.Request(base+path, headers={"Authorization": "Bearer "+token})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)
print("# Recent chats (fork + interview the ones with activity)\n")
print("# `[app]` rows are app-driven chats (created_by_app_id set): hidden from")
print("# the user's drawer but useful for the system-improvement brief. `updated` is the")
print("# cadence signal — interview the most recently/often active first.\n")
try:
    # include_app_chats=1 surfaces app-created chats too — they're excluded from
    # the owner's drawer history but are relevant to system-improvement review.
    chats = get("/api/chats?include_app_chats=1")
    chats = chats if isinstance(chats, list) else chats.get("chats", [])
    chats = sorted(chats, key=lambda c: c.get("updated_at",""), reverse=True)[:20]
    for c in chats:
        cid = c.get("id"); title = c.get("title") or "(untitled)"
        prov = c.get("provider") or "claude"
        updated = c.get("updated_at","")
        tag = "  [app]" if c.get("created_by_app_id") else ""
        print(f"- `{cid}`  [{prov}]{tag}  {title}  (updated {updated})")
    if not chats:
        print("(no chats)")
except Exception as e:
    print(f"(could not list chats: {e})")
PY

# app-feedback.md — cross-app feedback forms written under
# shared/app-feedback/<app-slug>/. Reflection can use these as durable
# product/editorial signals without needing to know each app's numeric id.
python3 - "$API_BASE_URL" "$SERVICE_TOKEN" >"$INPUTS/app-feedback.md" 2>>"$LOG" <<'PY' || true
import json, sys, urllib.parse, urllib.request
base, token = sys.argv[1].rstrip("/"), sys.argv[2]
headers = {"Authorization": "Bearer "+token}

def get_json(path):
    req = urllib.request.Request(base+path, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def list_entries(prefix, limit=500, max_pages=20):
    cursor = None
    seen = set()
    entries = []
    for _ in range(max_pages):
        path = "/api/storage/shared-list/" + urllib.parse.quote(prefix.strip("/"), safe="/")
        params = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        path += "?" + urllib.parse.urlencode(params)
        data = get_json(path)
        entries.extend(data.get("entries", []))
        nxt = data.get("next_cursor")
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
    return entries

print("# Recent app feedback\n")
try:
    entries = []
    app_dirs = []
    for entry in list_entries("app-feedback"):
        name = entry.get("name")
        path = entry.get("path")
        if entry.get("type") == "dir" and isinstance(path, str):
            app_dirs.append(path)
        elif entry.get("type") == "dir" and isinstance(name, str):
            app_dirs.append("app-feedback/" + name)
        elif entry.get("type") == "file" and str(name or "").endswith(".json"):
            entries.append(entry)
    for app_dir in sorted(set(app_dirs)):
        for entry in list_entries(app_dir):
            if entry.get("type") == "file" and str(entry.get("name", "")).endswith(".json"):
                entries.append(entry)
    entries = sorted(entries, key=lambda e: e.get("modified_at", ""), reverse=True)[:20]
    if not entries:
        print("(no app feedback)")
    for entry in entries:
        path = entry.get("path") or f"app-feedback/{entry.get('name','')}"
        try:
            item = get_json("/api/storage/shared/" + urllib.parse.quote(path, safe="/"))
            app = item.get("app") or item.get("app_id") or "app"
            signal = item.get("signal") or "note"
            date = item.get("report_date") or item.get("created_at") or ""
            text = (item.get("text") or "").replace("\n", " ").strip()
            print(f"- [{app}] {signal} {date}: {text or '(no note)'}")
        except Exception as exc:
            print(f"- {path}: could not read ({exc})")
except Exception as e:
    print(f"(could not list app feedback: {e})")
PY

# prev-report.html — yesterday's brief, so the agent doesn't repeat
# itself. Enumerate every cursor page and fetch the newest report.
PREV="$(API_BASE_URL="$API_BASE_URL" APP_ID="$APP_ID" SERVICE_TOKEN="$SERVICE_TOKEN" python3 - <<'PY' 2>>"$LOG"
import json, os, sys, urllib.parse, urllib.request

base = os.environ["API_BASE_URL"].rstrip("/")
app_id = os.environ["APP_ID"]
token = os.environ["SERVICE_TOKEN"]
headers = {"Authorization": f"Bearer {token}"}
cursor = None
seen = set()
reports = []

try:
    for _ in range(50):
        url = f"{base}/api/storage/apps-list/{app_id}/reports/"
        if cursor:
            url += "?" + urllib.parse.urlencode({"cursor": cursor})
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        for entry in data.get("entries", []):
            name = entry.get("name")
            if entry.get("type") == "file" and isinstance(name, str) and name.endswith(".html"):
                reports.append(name)
        nxt = data.get("next_cursor")
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
    print(sorted(reports)[-1] if reports else "")
except Exception as exc:
    print(f"could not enumerate previous reports: {exc}", file=sys.stderr)
    print("")
PY
)"
if [[ -n "$PREV" ]]; then
  curl -s "${auth[@]}" "$API_BASE_URL/api/storage/apps/$APP_ID/reports/$PREV" \
    >"$INPUTS/prev-report.html" 2>>"$LOG" || true
fi

# prev-question-answers.json — the partner's taps on the in-brief question
# cards a recent brief offered. The app saved them to
# question-answers/<date>.json (bare object). No live agent waited; they are
# read HERE, on the next run, so the agent can ACT on them in phase 2. Stage
# the single most recent answer file (filenames are <report_date>.json,
# ISO-sortable).
PREV_QA="$(API_BASE_URL="$API_BASE_URL" APP_ID="$APP_ID" SERVICE_TOKEN="$SERVICE_TOKEN" python3 - <<'PY' 2>>"$LOG"
import json, os, sys, urllib.parse, urllib.request

base = os.environ["API_BASE_URL"].rstrip("/")
app_id = os.environ["APP_ID"]
token = os.environ["SERVICE_TOKEN"]
headers = {"Authorization": f"Bearer {token}"}
cursor = None
seen = set()
files = []

try:
    for _ in range(50):
        url = f"{base}/api/storage/apps-list/{app_id}/question-answers/"
        if cursor:
            url += "?" + urllib.parse.urlencode({"cursor": cursor})
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        for entry in data.get("entries", []):
            name = entry.get("name")
            if entry.get("type") == "file" and isinstance(name, str) and name.endswith(".json"):
                files.append(name)
        nxt = data.get("next_cursor")
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
    print(sorted(files)[-1] if files else "")
except Exception as exc:
    # dir-not-created-yet (404) and any error both degrade to "no answers".
    print("", file=sys.stderr)
    print("")
PY
)"
if [[ -n "$PREV_QA" ]]; then
  curl -s "${auth[@]}" "$API_BASE_URL/api/storage/apps/$APP_ID/question-answers/$PREV_QA" \
    >"$INPUTS/prev-question-answers.json" 2>>"$LOG" || true
fi

# per-app-digest.json — compact per-app analytics summary the Reflection
# agent uses to triage which apps need attention tonight. Produced from
# two sources:
#   - activity.jsonl ON DISK for opens and durable app_signal events
#   - each app's legacy signals.jsonl during the runtime migration
# ~2–3 KB for 12 apps vs 10–100 KB of raw log; gives the agent a
# digest-first orientation so it doesn't burn turns re-reading raw events.
# Graceful on API errors: a failed app-read records has_signals:false and
# an error note rather than aborting the whole step.
DIGEST_TMP="$(mktemp "$INPUTS/.per-app-digest.json.XXXXXX" 2>>"$LOG" || true)"
if [[ -n "$DIGEST_TMP" ]] && APP_ID_FOR_DIGEST="$APP_ID" python3 - \
    "$API_BASE_URL" "$SERVICE_TOKEN" "$INPUTS" "$SINCE" \
    >"$DIGEST_TMP" 2>>"$LOG" <<'PY'
import hashlib, json, os, sys, urllib.request, urllib.error, datetime

base    = sys.argv[1].rstrip("/")
token   = sys.argv[2]
inp_dir = sys.argv[3]
expected_since = sys.argv[4]
headers = {"Authorization": "Bearer " + token}
now_utc = datetime.datetime.now(datetime.timezone.utc)
cutoff = datetime.datetime.fromisoformat(expected_since.replace("Z", "+00:00"))

# --- helpers ---

def api_get(path, timeout=20):
    req = urllib.request.Request(base + path, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def storage_get_text(app_id, path, timeout=15):
    """Fetch a text file from app storage; return None only when it is absent."""
    url = f"{base}/api/storage/apps/{app_id}/{path}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise

# --- activity: opens plus replay-safe app_signal events ---
activity_path = os.path.join(inp_dir, "activity.jsonl")
activity_status_path = os.path.join(inp_dir, "activity-status.json")
activity_source = {"ok": False, "error": "activity source status is missing"}
try:
    with open(activity_status_path, encoding="utf-8") as f:
        loaded_activity_source = json.load(f)
    if isinstance(loaded_activity_source, dict) and isinstance(
        loaded_activity_source.get("ok"), bool
    ):
        activity_source = loaded_activity_source
    else:
        activity_source = {"ok": False, "error": "activity source status is invalid"}
except Exception as exc:
    activity_source = {"ok": False, "error": f"activity source status unreadable: {exc}"}

opens_by_app = {}   # app_id (str) -> count
signal_counts_by_app = {}
error_signals_by_app = {}
app_errors_by_app = {}
recent_app_errors = {}
shell_errors = []
apps_with_signals = set()
seen_signal_ids = set()
if activity_source.get("ok") and not os.path.exists(activity_path):
    activity_source = {
        **activity_source,
        "ok": False,
        "error": "validated activity snapshot is missing",
    }
if activity_source.get("ok"):
    try:
        with open(activity_path, "rb") as f:
            snapshot = f.read()
        actual_sha = hashlib.sha256(snapshot).hexdigest()
        actual_count = sum(1 for line in snapshot.splitlines() if line.strip())
        if activity_source.get("since") != expected_since:
            raise ValueError("activity status belongs to a different observation window")
        if activity_source.get("sha256") != actual_sha:
            raise ValueError("activity snapshot hash does not match its status")
        if activity_source.get("event_count") != actual_count:
            raise ValueError("activity snapshot count does not match its status")
    except Exception as exc:
        activity_source = {
            **activity_source,
            "ok": False,
            "error": f"activity snapshot status mismatch: {exc}",
        }
if activity_source.get("ok"):
    try:
        with open(activity_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                aid = str(ev.get("app_id", ""))
                if ev.get("ev") == "app_error":
                    message = str(ev.get("message") or "")[:200]
                    summary = {
                        "ts": str(ev.get("ts") or ""),
                        "message": message,
                    }
                    if ev.get("where"):
                        summary["where"] = str(ev.get("where"))[:120]
                    if aid:
                        app_errors_by_app[aid] = app_errors_by_app.get(aid, 0) + 1
                        recent = recent_app_errors.setdefault(aid, [])
                        recent.append(summary)
                        del recent[:-5]
                    else:
                        shell_errors.append(summary)
                        del shell_errors[:-5]
                    continue
                if ev.get("ev") == "app_open" and aid:
                    opens_by_app[aid] = opens_by_app.get(aid, 0) + 1
                    continue
                if ev.get("ev") != "app_signal" or not aid:
                    continue
                signal_id = ev.get("id")
                if not isinstance(signal_id, str) or not signal_id:
                    continue
                signal_key = (aid, signal_id)
                if signal_key in seen_signal_ids:
                    continue
                occurred = ev.get("occurred_at", "")
                try:
                    occurred_at = datetime.datetime.fromisoformat(occurred.replace("Z", "+00:00"))
                    if occurred_at.tzinfo is None:
                        occurred_at = occurred_at.replace(tzinfo=datetime.timezone.utc)
                    if occurred_at < cutoff:
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                seen_signal_ids.add(signal_key)
                apps_with_signals.add(aid)
                sname = ev.get("name", "")
                if sname:
                    counts = signal_counts_by_app.setdefault(aid, {})
                    counts[sname] = counts.get(sname, 0) + 1
                if sname == "error":
                    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                    msg = payload.get("message") or payload.get("msg") or ""
                    if msg:
                        errors = error_signals_by_app.setdefault(aid, [])
                        errors.append((occurred_at, str(msg)[:200]))
                        errors.sort(key=lambda row: row[0])
                        del errors[:-5]
    except Exception as exc:
        # A status/snapshot mismatch must fail closed; never return partially
        # counted activity as though it represented the whole 24-hour window.
        opens_by_app.clear()
        signal_counts_by_app.clear()
        error_signals_by_app.clear()
        app_errors_by_app.clear()
        recent_app_errors.clear()
        shell_errors.clear()
        apps_with_signals.clear()
        seen_signal_ids.clear()
        activity_source = {
            **activity_source,
            "ok": False,
            "error": f"validated activity snapshot unreadable: {exc}",
        }

# --- fetch app list ---
try:
    apps = api_get("/api/apps/")
    if isinstance(apps, dict):
        apps = apps.get("apps", [])
except Exception as e:
    # API unavailable — write an empty digest so the agent knows it failed
    print(json.dumps({
        "_error": str(e), "activity_source": activity_source, "apps": [],
    }))
    sys.exit(0)

digests = []
for app in apps:
    app_id  = str(app.get("id", ""))
    slug    = app.get("name") or app.get("slug") or app_id
    name    = app.get("display_name") or slug
    if not app_id:
        continue

    opens_24h = opens_by_app.get(app_id, 0)

    # Parse signals.jsonl for this app from the storage API.
    signal_counts = dict(signal_counts_by_app.get(app_id, {}))
    error_signals = list(error_signals_by_app.get(app_id, []))
    has_signals   = app_id in apps_with_signals
    signals_error = None

    # Migration path: older cached runtimes wrote one per-app signals.jsonl.
    try:
        raw = storage_get_text(app_id, "signals.jsonl")
        if raw:
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    sig = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Count by name, limited to the last 24h
                ts_str = sig.get("ts", "")
                try:
                    ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    # Make tz-aware for comparison
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=datetime.timezone.utc)
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue
                sname = sig.get("name", "")
                if sname:
                    has_signals = True
                    signal_counts[sname] = signal_counts.get(sname, 0) + 1
                # Collect last-5 error messages (newest last in file → reverse later)
                if sname == "error":
                    msg = sig.get("message") or sig.get("msg") or ""
                    if msg:
                        error_signals.append((ts, str(msg)[:200]))
    except Exception as e:
        signals_error = str(e)[:200]

    entry = {
        "app_id":      app_id,
        "slug":        slug,
        "name":        name,
        "opens_24h":   opens_24h,
        "has_signals": has_signals,
        "signal_counts": signal_counts,
        "last_5_errors": [msg for _, msg in sorted(error_signals, key=lambda row: row[0])[-5:]],
        "app_errors_24h": app_errors_by_app.get(app_id, 0),
        "recent_app_errors": recent_app_errors.get(app_id, []),
    }
    if signals_error:
        entry["signals_read_error"] = signals_error
    digests.append(entry)

print(json.dumps({
    "generated_at": now_utc.isoformat(),
    "activity_source": activity_source,
    "shell_errors_24h": len(shell_errors),
    "recent_shell_errors": shell_errors,
    "apps": digests,
}, indent=2))
PY
then
  if python3 -m json.tool "$DIGEST_TMP" >/dev/null 2>>"$LOG"; then
    if mv -f -- "$DIGEST_TMP" "$INPUTS/per-app-digest.json"; then
      DIGEST_TMP=""
    else
      log "WARN could not atomically install per-app digest; retaining prior digest"
    fi
  else
    log "WARN per-app digest generation returned invalid JSON; retaining prior digest"
  fi
else
  log "WARN per-app digest generation failed; retaining prior digest"
fi
[[ -z "${DIGEST_TMP:-}" ]] || rm -f -- "$DIGEST_TMP"

# resource-snapshot.json — a cheap daily filesystem/cgroup pulse plus an
# adaptive deep /data inventory. The helper remembers its last complete deep
# scan and only walks the tree weekly, under pressure, or after unusual growth.
# This gives Reflection trend evidence without paying for the same broad `du`
# commands every night. History and decisions are bounded durable logs.
RESOURCE_MONITOR="$SCRIPT_DIR/resource_monitor.py"
RESOURCE_HISTORY="$DATA_DIR/apps/reflection/resource-history.jsonl"
RESOURCE_STATE="$DATA_DIR/apps/reflection/resource-monitor-state.json"
RESOURCE_LEDGER="$DATA_DIR/apps/reflection/resource-decisions.jsonl"
if [[ -r "$RESOURCE_MONITOR" ]]; then
  if ! python3 "$RESOURCE_MONITOR" snapshot \
      --data-dir "$DATA_DIR" \
      --output "$INPUTS/resource-snapshot.json" \
      --history "$RESOURCE_HISTORY" \
      --state "$RESOURCE_STATE" 2>>"$LOG"; then
    log "WARN resource monitor failed; resource snapshot may be stale"
  fi
else
  log "WARN resource monitor missing at $RESOURCE_MONITOR"
fi
if [[ -r "$RESOURCE_HISTORY" ]]; then
  tail -n 30 "$RESOURCE_HISTORY" >"$INPUTS/resource-history.jsonl" 2>>"$LOG" || true
else
  : >"$INPUTS/resource-history.jsonl"
fi
if [[ -r "$RESOURCE_LEDGER" ]]; then
  tail -n 100 "$RESOURCE_LEDGER" >"$INPUTS/resource-decisions.jsonl" 2>>"$LOG" || true
else
  : >"$INPUTS/resource-decisions.jsonl"
fi

# reflection-run-history.txt — bounded self-observation for the next agent.
# Metrics answer "did the last change make the run cheaper?"; the short log
# tail carries friction; git history prevents a later run from re-adding an
# experiment that an earlier run deliberately removed.
python3 - "$RUN_METRICS" "$LOG" "$DATA_DIR" >"$INPUTS/reflection-run-history.txt" 2>>"$LOG" <<'PY' || true
import json, pathlib, subprocess, sys

metrics_path, log_path, data_dir = map(pathlib.Path, sys.argv[1:])
print("# Reflection run history (bounded; newest last)\n")
print("## Run metrics")
try:
    rows = metrics_path.read_text(encoding="utf-8", errors="replace").splitlines()[-14:]
except OSError:
    rows = []
print("\n".join(rows) if rows else "(no prior metrics)")
print("\n## Recent reflection log tail")
try:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
except OSError:
    lines = []
print("\n".join(lines) if lines else "(no prior log)")
print("\n## Recent edits to reflection.md")
try:
    result = subprocess.run(
        ["git", "-C", str(data_dir), "log", "--oneline", "-10", "--",
         "shared/skills/reflection.md"],
        text=True, capture_output=True, timeout=10, check=False,
    )
    print(result.stdout.strip() or "(no recorded edits)")
except Exception as exc:
    print(f"(could not read skill history: {exc})")
PY

# Record the app id where the runner's goal message and the agent can
# find it (the agent writes reports to apps/<app_id>/reports/).
printf '%s\n' "$APP_ID" >"$INPUTS/app_id"
log "gathered inputs (activity + status, chats, feedback, app digest, resource snapshot + decisions) into $INPUTS/"

# --- heartbeat: prove liveness while the long run is in flight --------
# A background loop touches the heartbeat file every 60s. A monitor (or a
# morning glance) can `stat` it to tell "still reflection" from "wedged".
# Killed in the cleanup trap below.
#
# fd 9 (the flock handle) is CLOSED in the child (`9>&-`) so the lock is
# held ONLY by the main process. Without this, the backgrounded child
# inherits fd 9 and keeps the lock alive past the parent's exit until the
# child is reaped — so the NEXT night's run would spuriously see "another
# run holds the lock" and skip. The cleanup trap kills the child and
# waits for it so the lock is fully released by the time we exit.
heartbeat_loop() {
  local sleep_pid=""
  # A backgrounded shell function gets its own PID, but a plain `sleep 60`
  # inside it is a separate child. Killing only the function used to orphan
  # that sleep until its timeout (and kept captured stdout pipes open in tests).
  # Retire the active child before the heartbeat process exits.
  trap '[[ -z "$sleep_pid" ]] || kill "$sleep_pid" 2>/dev/null || true; exit 0' TERM INT
  while true; do
    date -Iseconds >"$HEARTBEAT" 2>/dev/null || true
    sleep 60 &
    sleep_pid=$!
    wait "$sleep_pid" 2>/dev/null || true
    sleep_pid=""
  done
}
heartbeat_loop 9>&- &
HEARTBEAT_PID=$!
cleanup() {
  if [[ -n "${HEARTBEAT_PID:-}" ]]; then
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# --- run the agent: full tools, real token, no sandbox ----------------
# The runner loads the reflection skill as the system prompt, sends the
# goal as the first user message, and drives the multi-turn loop. `timeout`
# bounds wall-clock; --signal=TERM gives the run a chance to flush before
# SIGKILL (--kill-after). The runner streams its own trace into $LOG.
RC=0
if [[ "${REFLECTION_DRY:-0}" == "1" ]]; then
  log "DRY run: skipping agent; recording dry outcome"
  RC=0
elif [[ ! -r "$RUNNER" ]]; then
  log "ERROR runner not found/readable at $RUNNER; exiting"
  RC=127
else
  timeout --signal=TERM --kill-after=60 "$RUN_TIMEOUT" \
    python3 "$RUNNER" >>"$LOG" 2>&1
  RC=$?
  if [[ "$RC" == "124" ]]; then
    log "WARN agent run hit the ${RUN_TIMEOUT}s timeout (terminated)"
  elif [[ "$RC" != "0" ]]; then
    log "WARN agent run exited non-zero (rc=$RC)"
  fi
fi

# --- final safety-net commit ------------------------------------------
# The agent commits as it goes (pm-commit per chunk). This is a backstop:
# if the run was killed mid-chunk, sweep any agent-touched files into one
# commit so nothing is left dirty + unreversible. pm-commit's denylist +
# 50-file guard keep this honest; --allow-broad because a full night can
# legitimately touch many files (skills, memory notes, app sources).
if command -v pm-commit >/dev/null 2>&1; then
  ( cd "$DATA_DIR" && pm-commit --allow-broad "reflection: nightly safety-net commit $DATE" \
      >>"$LOG" 2>&1 ) || true
fi

# --- deterministic morning-brief push ---------------------------------
# Delivery of the morning push is owned HERE, by the wrapper — NOT by the
# agent. The agent composes the brief and writes state.json (streak +
# one-line `last_summary` headline for the app header); the wrapper reads
# that headline and fires the push via the notifications API with the
# service token.
#
# Why the wrapper and not the agent: an agent-chosen notification tool
# proved unreliable. From 2026-06-30 the nightly agent began reaching for
# a leaked Claude Code harness `PushNotification` tool (found via
# `ToolSearch: select:PushNotification`) instead of the documented
# `curl /api/notifications/send`. That harness tool is a no-op inside
# Möbius, so no morning brief reached the partner for a week even though
# every run succeeded and every brief was written. Making the wrapper the
# sole sender — exactly as news/fetch.sh already does — removes the
# dependency on the agent picking the right tool. Best-effort: a failed
# push is logged, never fatal.
send_morning_push() {
  [[ "$RC" == "0" ]] || { log "morning push: skip (rc=$RC)"; return 0; }
  local brief="$DATA_DIR/apps/$APP_ID/reports/$DATE.html"
  [[ -f "$brief" ]] || { log "morning push: skip (no brief for $DATE)"; return 0; }
  # Trust the headline only if state.json was written by TODAY's run;
  # fall back to a generic line otherwise so the partner is still pinged.
  local headline
  headline="$(APP_ID="$APP_ID" DATE="$DATE" DATA_DIR="$DATA_DIR" python3 - <<'PY' 2>>"$LOG"
import json, os
try:
    s = json.load(open(f"{os.environ['DATA_DIR']}/apps/{os.environ['APP_ID']}/state.json"))
except Exception:
    s = {}
head = (s.get("last_summary") or "").strip()
print(head if str(s.get("last_run", "")).startswith(os.environ["DATE"]) else "")
PY
)"
  [[ -n "$headline" ]] || headline="Your nightly reflection is ready to read."
  local payload
  payload="$(APP_ID="$APP_ID" HEADLINE="$headline" python3 - <<'PY' 2>>"$LOG"
import json, os
app_id = os.environ["APP_ID"]
target = f"/shell/?app={app_id}"
print(json.dumps({
    "title": "Your morning brief is ready",
    "body": os.environ["HEADLINE"][:200],
    "source_type": "app",
    "source_id": app_id,
    "target": target,
    "actions": [{"action": "open_app", "title": "Read", "target": target}],
}))
PY
)"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${auth[@]}" \
    -H "Content-Type: application/json" -d "$payload" \
    "$API_BASE_URL/api/notifications/send" 2>>"$LOG")"
  case "$code" in
    200|201|204) log "morning push sent (http=$code)";;
    *)           log "WARN morning push failed (http=$code)";;
  esac
}
send_morning_push

# Persist one compact row about Reflection's own footprint. Keep the log
# bounded: it is an optimization input, not an audit trail. Cost/token details
# emitted by provider SDKs remain in the short reflection.log tail staged above.
RUN_FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
RUN_FINISHED_EPOCH="$(date +%s)"
RUN_DISK_AFTER="$(python3 - "$DATA_DIR" <<'PY' 2>/dev/null || echo 0
import shutil, sys
print(shutil.disk_usage(sys.argv[1]).used)
PY
)"
RUN_CPU_AFTER="$(awk '$1 == "usage_usec" {print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null || echo 0)"
python3 - "$RUN_METRICS" "$RUN_STARTED_AT" "$RUN_FINISHED_AT" \
    "$RUN_STARTED_EPOCH" "$RUN_FINISHED_EPOCH" "$RC" \
    "$RUN_DISK_BEFORE" "$RUN_DISK_AFTER" "$RUN_CPU_BEFORE" "$RUN_CPU_AFTER" \
    "$DATA_DIR/apps/$APP_ID/reports/$DATE.html" "${REFLECTION_DRY:-0}" \
    2>>"$LOG" <<'PY' || log "WARN could not persist reflection run metrics"
import json, os, pathlib, sys

(path, started_at, finished_at, started_epoch, finished_epoch, rc,
 disk_before, disk_after, cpu_before, cpu_after, report, dry) = sys.argv[1:]
def integer(value):
    try: return int(value)
    except (TypeError, ValueError): return 0
row = {
    "started_at": started_at,
    "finished_at": finished_at,
    "duration_seconds": max(0, integer(finished_epoch) - integer(started_epoch)),
    "exit_code": integer(rc),
    "dry_run": dry == "1",
    "brief_written": pathlib.Path(report).is_file(),
    "disk_used_delta_bytes": integer(disk_after) - integer(disk_before),
    "cgroup_cpu_usage_usec_delta": max(0, integer(cpu_after) - integer(cpu_before)),
}
target = pathlib.Path(path)
try:
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
except OSError:
    lines = []
lines = [line for line in lines if line.strip()][-59:]
lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
target.parent.mkdir(parents=True, exist_ok=True)
tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
with tmp.open("w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
    handle.flush(); os.fsync(handle.fileno())
os.replace(tmp, target)
PY

# --- emit cron_outcome ------------------------------------------------
emit_outcome "$RC"

log "done (rc=$RC)"
exit "$RC"
