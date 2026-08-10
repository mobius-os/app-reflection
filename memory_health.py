#!/usr/bin/env python3
"""Build a compact, content-free health handoff from Memory to Reflection."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
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


def _recall_activity(app_state: Path, now: dt.datetime) -> dict[str, Any]:
  """Count how many chats recalled per day, so a silent collapse becomes visible.

  The nightly audit grades the reads that happened; nothing watches whether reads
  happen at all, so a recall regression can run for a day or more before someone
  notices it by hand.

  Count DISTINCT chats, not read lines: one chat recalling ten times is a single
  agent deciding once, and raw volume lets a burst on either side of a quiet
  window hide a broad collapse. A day far below its own trailing baseline is the
  signal. Use a bounded nonzero baseline so several silent days cannot redefine
  a collapse as normal, and require three active days so a lone burst cannot
  become the baseline for a young or genuinely quiet log.
  """
  per_day: dict[str, set[str]] = {}
  try:
    logs = sorted((app_state / "read-log").glob("*.jsonl"))
  except OSError:
    logs = []
  for item in logs:
    chats = per_day.setdefault(item.stem, set())
    try:
      lines = item.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
      continue
    for line in lines:
      try:
        row = json.loads(line)
      except ValueError:
        continue
      chat_id = row.get("chat_id") if isinstance(row, dict) else None
      if isinstance(chat_id, str) and chat_id:
        chats.add(chat_id)

  last_full_day = now.date() - dt.timedelta(days=1)
  recent_counts = [
    len(per_day.get((last_full_day - dt.timedelta(days=offset)).isoformat(), ()))
    for offset in range(1, 15)
  ]
  baseline = [count for count in recent_counts if count > 0]
  baseline_median = statistics.median(baseline) if len(baseline) >= 3 else 0
  return {
    "last_full_day": last_full_day.isoformat(),
    "chats_recalling_last_full_day": len(per_day.get(last_full_day.isoformat(), ())),
    "baseline_median": baseline_median,
    "days_observed": len(per_day),
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


def build_health(memory_root: Path, *, now: dt.datetime | None = None) -> dict[str, Any]:
  """Summarize operational state without exposing chats, facts, or note bodies."""
  now = (now or _now()).astimezone(dt.timezone.utc)
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
  # One signal, not a severity tier: zero recalls and one-out-of-twenty are the
  # same failure, and a collapse only means anything against a baseline the log
  # is long enough to support.
  recall = _recall_activity(app_state, now)
  if (
    recall["baseline_median"] >= 3
    and recall["chats_recalling_last_full_day"] * 4 < recall["baseline_median"]
  ):
    reasons.append("recall_collapsed")
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
  parser.add_argument("--output", required=True)
  args = parser.parse_args(argv)
  _atomic_json(Path(args.output), build_health(Path(args.memory_root)))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
