#!/usr/bin/env python3
"""Build a compact, content-free health handoff from Memory to Reflection."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


VERSION = 1
_TERMINAL_STATUSES = {"published", "failed", "degraded", "abandoned"}
_RUN_FIELDS = (
  "status", "started_at", "finished_at", "app_id", "run_id", "commit",
  "new_commit", "provider", "model", "error_class", "error_code",
  "offending_path", "invalid_source_count", "source_chat_count",
  "queued_chat_count", "reason",
)


def _now() -> dt.datetime:
  return dt.datetime.now(dt.timezone.utc)


def _parse_time(value: Any) -> dt.datetime | None:
  if not isinstance(value, str):
    return None
  try:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=dt.timezone.utc)
  return parsed.astimezone(dt.timezone.utc)


def _read_json(path: Path) -> Any:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return None


def _read_runs(path: Path) -> list[dict[str, Any]]:
  rows = []
  try:
    items = sorted(path.glob("*.jsonl"))
  except OSError:
    return rows
  for item in items:
    try:
      lines = item.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
      continue
    for line in lines:
      try:
        row = json.loads(line)
      except ValueError:
        continue
      if isinstance(row, dict):
        rows.append(row)
  rows.sort(key=lambda row: str(row.get("finished_at") or row.get("started_at") or ""))
  return rows


def _safe_run(row: dict[str, Any] | None) -> dict[str, Any] | None:
  if not row:
    return None
  return {key: row[key] for key in _RUN_FIELDS if key in row}


def _safe_queue_progress(row: dict[str, Any] | None) -> dict[str, int] | None:
  value = row.get("chat_queue_progress") if isinstance(row, dict) else None
  if not isinstance(value, dict):
    return None
  result = {
    key: value[key]
    for key in ("pending_before_ack", "acknowledged", "remaining")
    if isinstance(value.get(key), int) and not isinstance(value.get(key), bool)
  }
  return result if len(result) == 3 else None


def _run_time(row: dict[str, Any] | None) -> dt.datetime | None:
  if not row:
    return None
  return _parse_time(row.get("finished_at") or row.get("started_at"))


def _rejection_codes(row: dict[str, Any] | None) -> list[str]:
  attempts = row.get("attempted_agents") if isinstance(row, dict) else None
  if not isinstance(attempts, list):
    return []
  return list(dict.fromkeys(
    attempt.get("rejection_code") for attempt in attempts
    if isinstance(attempt, dict) and isinstance(attempt.get("rejection_code"), str)
  ))


def _recall_activity(
  app_state: Path,
  now: dt.datetime,
  since: dt.datetime,
) -> dict[str, Any]:
  """Stage recall attempts since Reflection last completed, oldest first."""
  read_log = app_state / "read-log"
  available_days = []
  try:
    candidates = read_log.glob("*.jsonl")
  except OSError:
    candidates = ()
  for item in candidates:
    try:
      available_days.append(dt.date.fromisoformat(item.stem))
    except ValueError:
      continue
  available_days = sorted(day for day in available_days if day <= now.date())
  if not available_days:
    return {
      "since": since.isoformat(),
      "through": now.isoformat(),
      "days": [],
      "chat_days": 0,
    }
  start_day = max(since.date(), available_days[0])
  days = []
  cursor = start_day
  while cursor <= now.date():
    day = cursor.isoformat()
    item = read_log / f"{day}.jsonl"
    try:
      lines = item.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
      lines = []
    chats = set()
    for line in lines:
      try:
        row = json.loads(line)
      except ValueError:
        continue
      if not isinstance(row, dict):
        continue
      at = _parse_time(row.get("at"))
      if at is None or at < since or at > now:
        continue
      chat_id = row.get("chat_id") if isinstance(row, dict) else None
      if isinstance(chat_id, str) and chat_id:
        chats.add(chat_id)
    days.append({"date": day, "chats": len(chats)})
    cursor += dt.timedelta(days=1)
  return {
    "since": since.isoformat(),
    "through": now.isoformat(),
    "days": days,
    "chat_days": sum(day["chats"] for day in days),
  }


def _graph_counts(path: Path) -> dict[str, Any] | None:
  graph = _read_json(path)
  if not isinstance(graph, dict):
    return None
  nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
  edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
  problems = graph.get("problems") if isinstance(graph.get("problems"), list) else []
  warnings = sum(
    1 for problem in problems
    if isinstance(problem, dict) and problem.get("severity") == "warning"
  )
  return {
    "nodes": len(nodes),
    "edges": len(edges),
    "problems": len(problems),
    "warnings": warnings,
    "blocking_problems": len(problems) - warnings,
  }


def build_health(
  memory_root: Path,
  *,
  now: dt.datetime | None = None,
  since: str = "1970-01-01T00:00:00Z",
) -> dict[str, Any]:
  """Summarize operational state without exposing chats, facts, or note bodies."""
  now = (now or _now()).astimezone(dt.timezone.utc)
  recall_since = _parse_time(since)
  if recall_since is None:
    raise ValueError("since must be a timezone-aware ISO timestamp")
  app_state = memory_root / "app-state"
  runs = [
    row for row in _read_runs(app_state / "run-log")
    if row.get("status") in _TERMINAL_STATUSES
  ]
  current = _read_json(app_state / "run-status.json")
  latest_terminal = runs[-1] if runs else None
  current_is_newer = isinstance(current, dict) and (
    latest_terminal is None
    or (_run_time(current) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    >= (_run_time(latest_terminal) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
  )
  latest = current if current_is_newer else latest_terminal

  # run-status is written before the append-only receipt. If that final append
  # fails, fold the canonical terminal status into the same sequence that owns
  # every publish/failure metric instead of overriding only the headline.
  terminal_runs = list(runs)
  if current_is_newer and current.get("status") in _TERMINAL_STATUSES:
    run_id = current.get("run_id")
    if run_id:
      terminal_runs = [row for row in terminal_runs if row.get("run_id") != run_id]
    elif current in terminal_runs:
      terminal_runs.remove(current)
    terminal_runs.append(current)
  latest_terminal = terminal_runs[-1] if terminal_runs else None
  published = [row for row in terminal_runs if row.get("status") == "published"]
  failed = [
    row for row in terminal_runs if row.get("status") in {"failed", "degraded"}
  ]
  latest_publish = published[-1] if published else None
  latest_failure = failed[-1] if failed else None

  consecutive_unsuccessful = 0
  for row in reversed(terminal_runs):
    if row.get("status") == "published":
      break
    consecutive_unsuccessful += 1

  publish_time = _parse_time(
    (latest_publish or {}).get("finished_at") or (latest_publish or {}).get("started_at")
  )
  days_since_publish = None
  if publish_time:
    days_since_publish = round(max(0.0, (now - publish_time).total_seconds() / 86400), 2)

  failure_time = _parse_time((latest_failure or {}).get("finished_at"))
  recovered_after_failure = bool(
    publish_time and failure_time and publish_time > failure_time
  )
  graph = _graph_counts(memory_root / "repository" / "graph.json")
  pending = _read_json(app_state / "pending-chat-ids.json")
  pending_ids = pending.get("chat_ids") if isinstance(pending, dict) else []
  pending_count = len(pending_ids) if isinstance(pending_ids, list) else 0
  pending_capacity = pending.get("capacity") if isinstance(pending, dict) else None

  reasons = []
  advisories = []
  if latest and latest.get("status") == "running":
    reasons.append("latest_run_still_running")
  elif latest and latest.get("status") != "published":
    reasons.append("latest_run_unsuccessful")
  if consecutive_unsuccessful >= 2:
    reasons.append("repeated_unsuccessful_runs")
  if days_since_publish is None:
    reasons.append("no_published_run_observed")
  elif days_since_publish >= 2:
    reasons.append("publish_stale")
  if graph and graph["blocking_problems"]:
    reasons.append("blocking_graph_problems")
  if isinstance(pending_capacity, int) and pending_count >= pending_capacity:
    reasons.append("pending_chat_queue_at_capacity")
  recall = _recall_activity(app_state, now, recall_since)
  if graph and graph["warnings"]:
    advisories.append("graph_warnings_present")
  if recovered_after_failure:
    advisories.append("recovered_after_recent_failure")
  if pending_count:
    advisories.append("pending_chat_retry_backlog")

  return {
    "version": VERSION,
    "generated_at": now.isoformat(),
    "available": bool(latest or graph),
    "needs_attention": bool(reasons),
    "reasons": reasons,
    "advisories": advisories,
    "consecutive_unsuccessful_runs": consecutive_unsuccessful,
    "days_since_last_publish": days_since_publish,
    "pending_chat_count": pending_count,
    "pending_chat_capacity": pending_capacity,
    "queue_progress": _safe_queue_progress(latest_terminal),
    "recovered_after_failure": recovered_after_failure,
    "recall_activity": recall,
    "last_run": _safe_run(latest),
    "latest_terminal_run": _safe_run(latest_terminal),
    "last_rejection_codes": _rejection_codes(latest),
    "last_failure": _safe_run(latest_failure),
    "latest_graph": graph,
    "writer_contract": {
      "owner": "memory",
      "reflection_may_write_graph": False,
      "reflection_role": "observe, diagnose, and surface bounded recommendations",
    },
  }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
  tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--memory-root", required=True)
  parser.add_argument("--since", required=True)
  parser.add_argument("--output", required=True)
  args = parser.parse_args(argv)
  _atomic_json(
    Path(args.output),
    build_health(Path(args.memory_root), since=args.since),
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
