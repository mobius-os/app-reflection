#!/usr/bin/env python3
"""Deterministic input preparation for Reflection's thin shell supervisor."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


_MANIFEST_INPUTS = (
  ("activity-status.json", True),
  ("activity.jsonl", True),
  ("chats-status.json", True),
  ("chats.md", True),
  ("app-feedback.md", True),
  ("per-app-digest.json", True),
  ("tool-friction.json", True),
  ("housekeeping.json", True),
  ("memory-health.json", True),
  ("personalization-profile.json", True),
  ("resource-snapshot.json", True),
  ("resource-history.jsonl", True),
  ("resource-decisions.jsonl", True),
  ("meta-state.md", True),
  ("meta-state-status.json", True),
  ("meta-learning.jsonl", True),
  ("experiments.jsonl", True),
  ("experiment-status.json", True),
  ("reflection-run-history.txt", True),
  ("effort-summary.json", True),
  ("learning-loop.json", True),
  ("app_id", True),
  ("prev-report-name.txt", False),
  ("prev-report.html", False),
  ("prev-question-answers.json", False),
)

_EPOCH = "1970-01-01T00:00:00Z"


def _atomic_json(path: Path, value: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
  with temp.open("w", encoding="utf-8") as handle:
    json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
  os.replace(temp, path)


def _atomic_text(path: Path, value: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
  with temp.open("w", encoding="utf-8") as handle:
    handle.write(value)
    handle.flush()
    os.fsync(handle.fileno())
  os.replace(temp, path)


def _manifest_time(value: str) -> datetime.datetime:
  parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
  if parsed.tzinfo is None:
    raise ValueError("manifest run start must include a timezone")
  return parsed.astimezone(datetime.timezone.utc)


def _utc_time(value: str) -> datetime.datetime:
  """Parse platform timestamps, whose SQLite form may be timezone-naive UTC."""
  parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=datetime.timezone.utc)
  return parsed.astimezone(datetime.timezone.utc)


def _latest_completed_run(metrics: Path) -> str | None:
  try:
    lines = metrics.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError:
    return None
  for line in reversed(lines):
    try:
      row = json.loads(line)
    except ValueError:
      continue
    if not isinstance(row, dict):
      continue
    if (
      row.get("exit_code") != 0
      or row.get("dry_run") is not False
      or row.get("brief_written") is not True
    ):
      continue
    try:
      started = _manifest_time(row["started_at"])
    except (KeyError, TypeError, ValueError):
      continue
    return started.strftime("%Y-%m-%dT%H:%M:%SZ")
  return None


def resume_since(checkpoint: Path, metrics: Path | None = None) -> str:
  """Return the durable Reflection checkpoint, migrating old run metrics once."""
  try:
    value = _json_object(checkpoint).get("since")
    return _manifest_time(value).strftime("%Y-%m-%dT%H:%M:%SZ")
  except (OSError, TypeError, ValueError):
    pass
  migrated = _latest_completed_run(metrics) if metrics is not None else None
  if migrated:
    _atomic_json(checkpoint, {"schema": 1, "since": migrated})
    return migrated
  return _EPOCH


def advance_checkpoint(
  checkpoint: Path,
  *,
  started_at: str,
  report: Path,
  exit_code: int,
  dry_run: bool,
  inputs_complete: bool = True,
) -> bool:
  """Advance only after this real run successfully wrote its own brief."""
  if exit_code != 0 or dry_run or not inputs_complete:
    return False
  started = _manifest_time(started_at)
  try:
    modified = datetime.datetime.fromtimestamp(
      report.stat().st_mtime, datetime.timezone.utc,
    )
  except OSError:
    return False
  if modified < started:
    return False
  _atomic_json(checkpoint, {
    "schema": 1,
    "since": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
  })
  return True


def _json_object(path: Path) -> dict:
  value = json.loads(path.read_text(encoding="utf-8"))
  if not isinstance(value, dict):
    raise ValueError("expected a JSON object")
  return value


def _manifest_status(inputs: Path, name: str, current: bool) -> tuple[str, str | None]:
  """Classify one staged item without treating retained bytes as fresh evidence."""
  path = inputs / name
  if not current:
    return "stale", "file predates this run"
  try:
    if name == "activity-status.json":
      if not _json_object(path).get("ok"):
        return "unavailable", "current activity fetch did not validate"
    elif name == "activity.jsonl":
      status = _json_object(inputs / "activity-status.json")
      if not status.get("ok"):
        return "stale", "retained snapshot is not this run's activity window"
      content = path.read_bytes()
      if hashlib.sha256(content).hexdigest() != status.get("sha256"):
        return "unavailable", "snapshot hash does not match activity status"
    elif name == "chats-status.json":
      status = _json_object(path)
      if not status.get("active_ok"):
        return "unavailable", "current active chat summaries were not staged"
      if not status.get("deleted_complete"):
        return "partial", "recoverable-deleted chat summaries were not staged"
    elif name == "chats.md":
      status = _json_object(inputs / "chats-status.json")
      if not status.get("active_ok"):
        return "stale", "retained chat digest is not this run's active view"
      content = path.read_bytes()
      if hashlib.sha256(content).hexdigest() != status.get("sha256"):
        return "unavailable", "chat digest hash does not match chat status"
      if not status.get("deleted_complete"):
        return "partial", "recoverable-deleted chat summaries were not staged"
    elif name == "per-app-digest.json":
      source = _json_object(path).get("activity_source")
      if not isinstance(source, dict) or not source.get("ok"):
        return "partial", "digest has no validated current activity window"
    elif name == "housekeeping.json":
      state = str(_json_object(path).get("status") or "unavailable")
      if state != "ok":
        return ("partial" if state == "partial" else "unavailable"), (
          f"housekeeping status is {state}"
        )
    elif name == "memory-health.json":
      if not _json_object(path).get("available"):
        return "unavailable", "Memory health handoff is unavailable"
    elif name == "personalization-profile.json":
      if not _json_object(path).get("available"):
        return "unavailable", "Memory personalization profile is unavailable"
    elif name == "meta-state-status.json":
      if not _json_object(path).get("exists"):
        return "unavailable", "canonical operating model is unavailable"
  except (OSError, UnicodeError, ValueError) as exc:
    return "unavailable", f"could not validate staged item: {exc}"
  return "complete", None


def build_input_manifest(
  inputs: Path,
  *,
  run_id: str,
  started_at: str,
) -> dict:
  """Atomically describe the one evidence bundle Reflection may trust tonight."""
  started = _manifest_time(started_at)
  items = []
  for name, required in _MANIFEST_INPUTS:
    path = inputs / name
    entry: dict = {"path": name, "required": required}
    try:
      stat = path.stat()
      if not path.is_file() or path.is_symlink():
        raise OSError("not a regular file")
    except OSError as exc:
      entry["status"] = "unavailable" if required else "absent"
      if required:
        entry["reason"] = f"not staged: {exc}"
      items.append(entry)
      continue
    modified = datetime.datetime.fromtimestamp(
      stat.st_mtime, datetime.timezone.utc,
    )
    status, reason = _manifest_status(inputs, name, modified >= started)
    raw = path.read_bytes()
    entry.update({
      "status": status,
      "bytes": len(raw),
      "sha256": hashlib.sha256(raw).hexdigest(),
      "modified_at": modified.isoformat(),
    })
    if reason:
      entry["reason"] = reason[:500]
    items.append(entry)
  required_items = [item for item in items if item["required"]]
  counts = {
    state: sum(item["status"] == state for item in items)
    for state in ("complete", "partial", "unavailable", "stale", "absent")
  }
  payload = {
    "schema": 1,
    "run_id": run_id[:160],
    "started_at": started.isoformat(),
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "status": (
      "complete"
      if all(item["status"] == "complete" for item in required_items)
      else "partial"
    ),
    "counts": counts,
    "items": items,
  }
  _atomic_json(inputs / "input-manifest.json", payload)
  return payload


def write_activity_status(
  target: Path,
  *,
  ok: bool,
  error: str,
  event_count: int | None,
  since: str,
  sha256: str | None,
) -> None:
  payload = {
    "ok": ok,
    "since": since,
    "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
  }
  if event_count is not None:
    payload["event_count"] = event_count
  if sha256:
    payload["sha256"] = sha256
  if error:
    payload["error"] = error[:500]
  if not ok:
    payload["retained_previous_snapshot"] = (
      target.parent / "activity.jsonl"
    ).exists()
  _atomic_json(target, payload)


def validate_activity(path: Path) -> int:
  count = 0
  with path.open(encoding="utf-8") as handle:
    for line_number, line in enumerate(handle, 1):
      if not line.strip():
        continue
      try:
        event = json.loads(line)
      except json.JSONDecodeError as exc:
        raise ValueError(
          f"activity NDJSON line {line_number} is invalid: {exc}"
        ) from exc
      if (
        not isinstance(event, dict)
        or not isinstance(event.get("ev"), str)
        or not isinstance(event.get("ts"), str)
      ):
        raise ValueError(
          f"activity NDJSON line {line_number} lacks string ev/ts fields"
        )
      count += 1
  return count


def _api_json(base: str, token: str, path: str, timeout: int = 20):
  request = urllib.request.Request(
    base + path,
    headers={"Authorization": "Bearer " + token},
  )
  with urllib.request.urlopen(request, timeout=timeout) as response:
    return json.loads(response.read().decode("utf-8"))


def _chat_rows(value) -> list[dict]:
  if isinstance(value, dict):
    value = value.get("chats")
  if not isinstance(value, list):
    raise ValueError("chat discovery returned an invalid payload")
  return [row for row in value if isinstance(row, dict)]


def _one_line(value, fallback: str = "") -> str:
  text = " ".join(str(value or fallback).split())
  return text.replace("\\", "\\\\").replace("`", "\\`")


def stage_chat_digest(
  base: str,
  token: str,
  data_dir: Path,
  target: Path,
  status_target: Path,
  since: str,
) -> dict:
  """Stage every chat changed since the last completed run, oldest first."""
  base = base.rstrip("/")
  cutoff = _manifest_time(since)
  active_ok = False
  deleted_complete = False
  error = None
  chats: list[dict] = []

  try:
    active = _chat_rows(_api_json(
      base, token, "/api/chats?include_app_chats=1",
    ))
    active_ok = True
    by_id = {}
    for row in active:
      chat_id = row.get("id")
      recency = row.get("activity_at") or row.get("updated_at")
      try:
        changed = _utc_time(str(recency))
      except (TypeError, ValueError):
        continue
      if isinstance(chat_id, str) and changed > cutoff:
        by_id[chat_id] = {**row, "recency_at": recency}
    try:
      before = None
      seen_pages = set()
      while True:
        params = {"include_deleted": "true", "limit": "100"}
        if before:
          params.update({
            "before_recency": before["recency_at"],
            "before_id": before["id"],
          })
        deleted = _api_json(
          base, token, "/api/chat-logs?" + urllib.parse.urlencode(params),
        )
        if not isinstance(deleted, dict) or not isinstance(deleted.get("items"), list):
          raise ValueError("deleted chat discovery returned an invalid payload")
        reached_cutoff = False
        for row in deleted["items"]:
          if not isinstance(row, dict):
            continue
          recency = row.get("recency_at") or row.get("updated_at")
          try:
            changed = _utc_time(str(recency))
          except (TypeError, ValueError):
            continue
          if changed <= cutoff:
            reached_cutoff = True
            break
          if not row.get("deleted_at"):
            continue
          chat_id = row.get("id")
          if not isinstance(chat_id, str) or chat_id in by_id:
            continue
          by_id[chat_id] = {
            **row,
            "recency_at": recency,
            "provider": "unknown",
          }
        if reached_cutoff or deleted.get("next_before") is None:
          break
        next_before = deleted.get("next_before")
        if not isinstance(next_before, dict):
          raise ValueError("deleted chat discovery returned an invalid cursor")
        page_key = (next_before.get("recency_at"), next_before.get("id"))
        if not all(isinstance(value, str) and value for value in page_key):
          raise ValueError("deleted chat discovery returned an invalid cursor")
        if page_key in seen_pages:
          raise ValueError("deleted chat discovery repeated its cursor")
        seen_pages.add(page_key)
        before = {"recency_at": page_key[0], "id": page_key[1]}
      deleted_complete = True
    except Exception as exc:
      error = f"deleted chat discovery failed ({type(exc).__name__})"
    chats = sorted(
      by_id.values(),
      key=lambda row: str(row.get("recency_at") or row.get("updated_at") or ""),
    )
  except Exception as exc:
    error = f"active chat discovery failed ({type(exc).__name__})"

  lines = [
    "# Chats awaiting Reflection review (oldest first)",
    "",
    "`[app]` rows are app-driven chats: hidden from the owner's drawer but",
    "useful for the system-improvement brief. `[deleted]` rows remain evidence",
    "during their recovery window, but must be read directly rather than forked.",
    "",
  ]
  for row in chats:
    chat_id = row.get("id")
    if not isinstance(chat_id, str):
      continue
    display_id = _one_line(chat_id)
    title = _one_line(row.get("title"), "(untitled)")
    provider = _one_line(row.get("provider"), "claude")
    updated = _one_line(row.get("recency_at") or row.get("updated_at"))
    tags = []
    if row.get("created_by_app_id"):
      tags.append("app")
    if row.get("deleted_at"):
      tags.append("deleted")
    tag = "".join(f"  [{item}]" for item in tags)
    message_count = row.get("message_count", row.get("messages_count"))
    if message_count is None and isinstance(row.get("messages"), list):
      message_count = len(row["messages"])
    metrics = []
    if isinstance(message_count, int) and not isinstance(message_count, bool):
      metrics.append(f"messages={message_count}")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", chat_id):
      note = data_dir / "shared" / "memory" / "chats" / chat_id / "index.md"
      try:
        metrics.append(f"note_bytes={note.stat().st_size}")
      except OSError:
        metrics.append("note=absent")
    metric_text = ", ".join(metrics) if metrics else "size unavailable"
    lines.append(
      f"- `{display_id}`  [{provider}]{tag}  {title}  "
      f"(updated {updated}; {metric_text})"
    )
  if not chats:
    lines.append("(no chats available)" if not active_ok else "(no chats)")
  content = "\n".join(lines) + "\n"
  _atomic_text(target, content)
  status = {
    "schema": 1,
    "since": since,
    "active_ok": active_ok,
    "deleted_complete": deleted_complete,
    "chat_count": len(chats),
    "subject_ids": [
      row["id"] for row in chats if isinstance(row.get("id"), str)
    ],
    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
  }
  if error:
    status["error"] = error
  _atomic_json(status_target, status)
  return status


def _storage_text(
  base: str, token: str, app_id: str, path: str, timeout: int = 15,
) -> str | None:
  request = urllib.request.Request(
    f"{base}/api/storage/apps/{app_id}/{path}",
    headers={"Authorization": "Bearer " + token},
  )
  try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
      return response.read().decode("utf-8")
  except urllib.error.HTTPError as exc:
    if exc.code == 404:
      return None
    raise


def stage_question_answers(
  base: str,
  token: str,
  app_id: str,
  since: str,
  target: Path,
) -> int:
  """Stage every answer packet after the checkpoint, oldest first."""
  base = base.rstrip("/")
  cutoff = _manifest_time(since)
  cursor = None
  seen = set()
  entries = []
  while True:
    path = f"/api/storage/apps-list/{urllib.parse.quote(app_id, safe='')}/question-answers/"
    if cursor:
      path += "?" + urllib.parse.urlencode({"cursor": cursor})
    try:
      listing = _api_json(base, token, path)
    except urllib.error.HTTPError as exc:
      if exc.code == 404 and cursor is None:
        target.unlink(missing_ok=True)
        return 0
      raise
    if not isinstance(listing, dict) or not isinstance(listing.get("entries"), list):
      raise ValueError("question-answer listing returned an invalid payload")
    entries.extend(
      entry for entry in listing["entries"]
      if isinstance(entry, dict)
      and entry.get("type") == "file"
      and isinstance(entry.get("name"), str)
      and entry["name"].endswith(".json")
    )
    next_cursor = listing.get("next_cursor")
    if not next_cursor:
      break
    if not isinstance(next_cursor, str) or next_cursor in seen:
      raise ValueError("question-answer listing repeated its cursor")
    seen.add(next_cursor)
    cursor = next_cursor

  packets = []
  for entry in entries:
    name = entry["name"]
    path = (
      f"/api/storage/apps/{urllib.parse.quote(app_id, safe='')}/"
      f"question-answers/{urllib.parse.quote(name, safe='')}"
    )
    packet = _api_json(base, token, path)
    if not isinstance(packet, dict):
      continue
    raw_time = (
      packet.get("answered_at")
      or entry.get("modified_at")
      or (f"{packet.get('report_date')}T00:00:00Z" if packet.get("report_date") else None)
    )
    try:
      answered_at = _utc_time(str(raw_time))
    except (TypeError, ValueError):
      continue
    if answered_at > cutoff:
      packets.append((answered_at, name, packet))
  packets.sort(key=lambda item: (item[0], item[1]))
  if not packets:
    target.unlink(missing_ok=True)
    return 0
  _atomic_json(target, {
    "since": since,
    "answer_packets": [packet for _, _, packet in packets],
  })
  return len(packets)


def _add_request_error(groups: dict, event: dict) -> None:
  try:
    status = int(event.get("status"))
    count = max(1, int(event.get("count", 1)))
  except (TypeError, ValueError):
    return
  method = str(event.get("method") or "?")[:16]
  route = str(event.get("route") or "<unmatched>")[:240]
  key = (method, route, status)
  group = groups.setdefault(key, {
    "method": method,
    "route": route,
    "status": status,
    "count": 0,
    "peak_window_count": 0,
    "first_ts": str(event.get("first_ts") or event.get("ts") or ""),
    "last_ts": str(event.get("last_ts") or event.get("ts") or ""),
  })
  group["count"] += count
  group["peak_window_count"] = max(group["peak_window_count"], count)
  first_ts = str(event.get("first_ts") or event.get("ts") or "")
  last_ts = str(event.get("last_ts") or event.get("ts") or "")
  if first_ts and (not group["first_ts"] or first_ts < group["first_ts"]):
    group["first_ts"] = first_ts
  if last_ts and last_ts > group["last_ts"]:
    group["last_ts"] = last_ts


def _expected_storage_miss(event: dict) -> bool:
  try:
    status = int(event.get("status"))
  except (TypeError, ValueError):
    return False
  return (
    status == 404
    and str(event.get("method") or "").upper() in {"GET", "DELETE"}
    and str(event.get("route") or "")
    == "/api/storage/apps/{app_id}/{path:path}"
  )


def _top_request_errors(groups: dict) -> list[dict]:
  return sorted(
    groups.values(),
    key=lambda item: (-item["count"], item["status"], item["route"]),
  )[:5]


@dataclass
class ActivityDigest:
  opens: dict[str, int] = field(default_factory=dict)
  signal_counts: dict[str, dict[str, int]] = field(default_factory=dict)
  signal_errors: dict[str, list[tuple[datetime.datetime, str]]] = field(
    default_factory=dict,
  )
  app_errors: dict[str, int] = field(default_factory=dict)
  app_error_details: dict[str, list[dict]] = field(default_factory=dict)
  shell_errors: list[dict] = field(default_factory=list)
  request_errors: dict[str, dict] = field(default_factory=dict)
  shell_request_errors: dict = field(default_factory=dict)
  storage_misses: dict[str, int] = field(default_factory=dict)
  shell_storage_misses: int = 0
  apps_with_signals: set[str] = field(default_factory=set)
  legacy_signal_apps: set[str] = field(default_factory=set)
  ignored_events: int = 0


def _event_count(event: dict) -> int:
  try:
    return max(1, int(event.get("count", 1)))
  except (TypeError, ValueError):
    return 1


def _parse_activity_snapshot(
  source: dict,
  snapshot: bytes,
  cutoff: datetime.datetime,
) -> tuple[dict, ActivityDigest]:
  """Parse canonical activity once into the checkpoint-interval digest."""
  digest = ActivityDigest()
  if not source.get("ok"):
    return source, digest
  seen_signal_ids: set[tuple[str, str]] = set()
  try:
    for raw_line in snapshot.decode("utf-8").splitlines():
      if not raw_line.strip():
        continue
      event = json.loads(raw_line)
      if not isinstance(event, dict) or not isinstance(event.get("ev"), str):
        digest.ignored_events += 1
        continue
      raw_app_id = event.get("app_id")
      app_id = str(raw_app_id) if raw_app_id is not None else ""
      event_name = event.get("ev")
      if event_name == "request_error":
        if _expected_storage_miss(event):
          count = _event_count(event)
          if app_id:
            digest.storage_misses[app_id] = (
              digest.storage_misses.get(app_id, 0) + count
            )
          else:
            digest.shell_storage_misses += count
          continue
        groups = (
          digest.request_errors.setdefault(app_id, {})
          if app_id else digest.shell_request_errors
        )
        _add_request_error(groups, event)
      elif event_name == "app_error":
        summary = {
          "ts": str(event.get("ts") or ""),
          "message": str(event.get("message") or "")[:200],
        }
        if event.get("where"):
          summary["where"] = str(event.get("where"))[:120]
        if app_id:
          digest.app_errors[app_id] = digest.app_errors.get(app_id, 0) + 1
          digest.app_error_details.setdefault(app_id, []).append(summary)
        else:
          digest.shell_errors.append(summary)
      elif event_name == "app_open" and app_id:
        digest.opens[app_id] = digest.opens.get(app_id, 0) + 1
      elif (
        event_name == "storage_write"
        and app_id
        and event.get("path") == "signals.jsonl"
      ):
        # A legacy file can contain an in-window signal only when it was
        # written in this same activity window. This turns an all-app probe
        # into a targeted migration read without losing current evidence.
        digest.legacy_signal_apps.add(app_id)
      elif event_name == "app_signal" and app_id:
        signal_id = event.get("id")
        if not isinstance(signal_id, str) or not signal_id:
          digest.ignored_events += 1
          continue
        signal_key = (app_id, signal_id)
        if signal_key in seen_signal_ids:
          continue
        occurred = event.get("occurred_at", "")
        try:
          occurred_at = datetime.datetime.fromisoformat(
            occurred.replace("Z", "+00:00")
          )
          if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=datetime.timezone.utc)
          if occurred_at < cutoff:
            continue
        except (ValueError, TypeError, AttributeError):
          digest.ignored_events += 1
          continue
        signal_name = event.get("name", "")
        if not isinstance(signal_name, str):
          digest.ignored_events += 1
          continue
        seen_signal_ids.add(signal_key)
        digest.apps_with_signals.add(app_id)
        if signal_name:
          counts = digest.signal_counts.setdefault(app_id, {})
          counts[signal_name] = counts.get(signal_name, 0) + 1
        if signal_name == "error":
          payload = event.get("payload")
          payload = payload if isinstance(payload, dict) else {}
          message = payload.get("message") or payload.get("msg") or ""
          if message:
            errors = digest.signal_errors.setdefault(app_id, [])
            errors.append((occurred_at, str(message)[:200]))
            errors.sort(key=lambda row: row[0])
  except Exception as exc:
    return {
      **source,
      "ok": False,
      "error": f"validated activity snapshot unreadable: {exc}",
    }, ActivityDigest()
  if digest.ignored_events:
    source = {**source, "ignored_event_count": digest.ignored_events}
  return source, digest


def _merge_legacy_signals(
  raw: str | None,
  cutoff: datetime.datetime,
  counts: dict[str, int],
  errors: list[tuple[datetime.datetime, str]],
) -> bool:
  found = False
  for line in raw.splitlines() if raw else ():
    try:
      signal = json.loads(line)
      occurred_at = datetime.datetime.fromisoformat(
        str(signal.get("ts", "")).replace("Z", "+00:00")
      )
      if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=datetime.timezone.utc)
      if occurred_at < cutoff:
        continue
    except (json.JSONDecodeError, ValueError, TypeError):
      continue
    signal_name = signal.get("name", "")
    if signal_name:
      found = True
      counts[signal_name] = counts.get(signal_name, 0) + 1
    if signal_name == "error":
      message = signal.get("message") or signal.get("msg") or ""
      if message:
        errors.append((occurred_at, str(message)[:200]))
  return found


def _activity_source(inputs: Path, expected_since: str) -> tuple[dict, bytes]:
  status_path = inputs / "activity-status.json"
  activity_path = inputs / "activity.jsonl"
  try:
    source = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("ok"), bool):
      raise ValueError("activity source status is invalid")
  except Exception as exc:
    return {"ok": False, "error": f"activity source status unreadable: {exc}"}, b""
  if not source.get("ok"):
    return source, b""
  try:
    snapshot = activity_path.read_bytes()
    actual_sha = hashlib.sha256(snapshot).hexdigest()
    actual_count = sum(1 for line in snapshot.splitlines() if line.strip())
    if source.get("since") != expected_since:
      raise ValueError("activity status belongs to a different observation window")
    if source.get("sha256") != actual_sha:
      raise ValueError("activity snapshot hash does not match its status")
    if source.get("event_count") != actual_count:
      raise ValueError("activity snapshot count does not match its status")
    return source, snapshot
  except Exception as exc:
    return {
      **source,
      "ok": False,
      "error": f"activity snapshot status mismatch: {exc}",
    }, b""


def build_app_digest(
  base: str,
  token: str,
  inputs: Path,
  expected_since: str,
) -> dict:
  """Build one bounded, fail-closed app digest from a verified snapshot."""
  base = base.rstrip("/")
  now = datetime.datetime.now(datetime.timezone.utc)
  cutoff = datetime.datetime.fromisoformat(expected_since.replace("Z", "+00:00"))
  source, snapshot = _activity_source(inputs, expected_since)
  source, activity = _parse_activity_snapshot(source, snapshot, cutoff)

  try:
    apps = _api_json(base, token, "/api/apps/")
    if isinstance(apps, dict):
      apps = apps.get("apps", [])
    if not isinstance(apps, list):
      raise ValueError("apps response is not a list")
  except Exception as exc:
    return {"_error": str(exc), "activity_source": source, "apps": []}

  digests = []
  for app in apps:
    if not isinstance(app, dict):
      continue
    app_id = str(app.get("id", ""))
    if not app_id:
      continue
    slug = str(app.get("slug") or app_id)
    name = str(app.get("name") or slug)
    counts = dict(activity.signal_counts.get(app_id, {}))
    errors = list(activity.signal_errors.get(app_id, []))
    has_signals = app_id in activity.apps_with_signals
    signals_error = None
    if app_id in activity.legacy_signal_apps and not has_signals:
      try:
        raw = _storage_text(base, token, app_id, "signals.jsonl")
        has_signals = _merge_legacy_signals(
          raw, cutoff, counts, errors,
        )
      except Exception as exc:
        signals_error = str(exc)[:200]
    groups = activity.request_errors.get(app_id, {})
    entry = {
      "app_id": app_id,
      "slug": slug,
      "name": name,
      "opens_in_window": activity.opens.get(app_id, 0),
      "has_signals": has_signals,
      "signal_counts": counts,
      "signal_errors_in_window": [
        message for _, message in sorted(errors, key=lambda row: row[0])
      ],
      "app_error_count_in_window": activity.app_errors.get(app_id, 0),
      "app_errors_in_window": activity.app_error_details.get(app_id, []),
      "request_error_count_in_window": sum(
        group["count"] for group in groups.values()
      ),
      "top_request_errors": _top_request_errors(groups),
      "storage_miss_count_in_window": activity.storage_misses.get(app_id, 0),
    }
    if signals_error:
      entry["signals_read_error"] = signals_error
    digests.append(entry)
  return {
    "generated_at": now.isoformat(),
    "activity_source": source,
    "shell_error_count_in_window": len(activity.shell_errors),
    "shell_errors_in_window": activity.shell_errors,
    "shell_request_error_count_in_window": sum(
      group["count"] for group in activity.shell_request_errors.values()
    ),
    "top_shell_request_errors": _top_request_errors(
      activity.shell_request_errors,
    ),
    "shell_storage_miss_count_in_window": activity.shell_storage_misses,
    "apps": digests,
  }


def _main(argv: list[str]) -> int:
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers(dest="command", required=True)
  status = subparsers.add_parser("activity-status")
  status.add_argument("target", type=Path)
  status.add_argument("ok", choices=("true", "false"))
  status.add_argument("error")
  status.add_argument("event_count")
  status.add_argument("since")
  status.add_argument("sha256")
  validate = subparsers.add_parser("validate-activity")
  validate.add_argument("path", type=Path)
  chats = subparsers.add_parser("chats")
  chats.add_argument("base")
  chats.add_argument("token")
  chats.add_argument("data_dir", type=Path)
  chats.add_argument("target", type=Path)
  chats.add_argument("status_target", type=Path)
  chats.add_argument("since")
  checkpoint = subparsers.add_parser("resume-since")
  checkpoint.add_argument("checkpoint", type=Path)
  checkpoint.add_argument("metrics", type=Path, nargs="?")
  complete = subparsers.add_parser("complete-run")
  complete.add_argument("checkpoint", type=Path)
  complete.add_argument("started_at")
  complete.add_argument("report", type=Path)
  complete.add_argument("exit_code", type=int)
  complete.add_argument("dry_run", choices=("true", "false"))
  complete.add_argument("inputs_complete", choices=("true", "false"))
  answers = subparsers.add_parser("question-answers")
  answers.add_argument("base")
  answers.add_argument("token")
  answers.add_argument("app_id")
  answers.add_argument("since")
  answers.add_argument("target", type=Path)
  digest = subparsers.add_parser("app-digest")
  digest.add_argument("base")
  digest.add_argument("token")
  digest.add_argument("inputs", type=Path)
  digest.add_argument("since")
  manifest = subparsers.add_parser("manifest")
  manifest.add_argument("inputs", type=Path)
  manifest.add_argument("run_id")
  manifest.add_argument("started_at")
  args = parser.parse_args(argv)
  try:
    if args.command == "activity-status":
      write_activity_status(
        args.target,
        ok=args.ok == "true",
        error=args.error,
        event_count=int(args.event_count) if args.event_count else None,
        since=args.since,
        sha256=args.sha256 or None,
      )
    elif args.command == "validate-activity":
      print(validate_activity(args.path))
    elif args.command == "chats":
      print(json.dumps(stage_chat_digest(
        args.base,
        args.token,
        args.data_dir,
        args.target,
        args.status_target,
        args.since,
      )))
    elif args.command == "resume-since":
      print(resume_since(args.checkpoint, args.metrics))
    elif args.command == "complete-run":
      print("true" if advance_checkpoint(
        args.checkpoint,
        started_at=args.started_at,
        report=args.report,
        exit_code=args.exit_code,
        dry_run=args.dry_run == "true",
        inputs_complete=args.inputs_complete == "true",
      ) else "false")
    elif args.command == "question-answers":
      print(stage_question_answers(
        args.base,
        args.token,
        args.app_id,
        args.since,
        args.target,
      ))
    elif args.command == "app-digest":
      print(json.dumps(
        build_app_digest(args.base, args.token, args.inputs, args.since),
        indent=2,
      ))
    else:
      build_input_manifest(
        args.inputs,
        run_id=args.run_id,
        started_at=args.started_at,
      )
  except (OSError, ValueError) as exc:
    print(str(exc), file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(_main(sys.argv[1:]))
