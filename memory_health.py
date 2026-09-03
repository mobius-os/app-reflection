#!/usr/bin/env python3
"""Build a compact health and learning handoff from Memory to Reflection."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


VERSION = 1
_TERMINAL_STATUSES = {"published", "failed", "degraded", "abandoned"}
_RUN_FIELDS = (
  "status", "started_at", "finished_at", "app_id", "run_id", "commit",
  "new_commit", "provider", "model", "error_class", "error_code",
  "offending_path", "invalid_source_count", "source_chat_count",
  "queued_chat_count", "reason", "model_work", "recall_audit_model_work",
  "recall_model_work",
  "read_audit_count", "writer_self_reviews", "deferred_read_audit_count",
)
_SELF_REVIEW_FIELDS = (
  "hardest_decision", "possibly_missed", "prompt_change", "next_experiment",
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


def _publication_receipt(
  memory_root: Path,
  run: dict[str, Any] | None,
) -> dict[str, Any]:
  """Describe whether one terminal Memory result is safe to consume.

  ``run-status.json`` becomes terminal only after the writer has finished its
  work. A published result has one additional atomic boundary: ``.ready`` must
  point at the same commit. Keeping that equality in the handoff means
  Reflection records the exact immutable Memory revision it assessed instead
  of merely observing that some run ended.
  """
  ready = _read_json(memory_root / ".ready")
  ready_commit = ready.get("commit") if isinstance(ready, dict) else None
  status = run.get("status") if isinstance(run, dict) else None
  commit = run.get("commit") if isinstance(run, dict) else None
  terminal = status in _TERMINAL_STATUSES
  publication_matches = (
    status == "published"
    and isinstance(commit, str)
    and bool(commit)
    and commit == ready_commit
  )
  finalized = terminal and (status != "published" or publication_matches)
  return {
    "finalized": finalized,
    "status": status,
    "run_id": run.get("run_id") if isinstance(run, dict) else None,
    "started_at": run.get("started_at") if isinstance(run, dict) else None,
    "finished_at": run.get("finished_at") if isinstance(run, dict) else None,
    "commit": commit,
    "ready_commit": ready_commit,
    "publication_matches": publication_matches,
  }


def await_finalized_run(
  memory_root: Path,
  *,
  timeout_seconds: float,
  poll_seconds: float,
  monotonic: Callable[[], float] = time.monotonic,
  sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
  """Wait only while Memory has an active or not-yet-published current run.

  An absent status is not a dependency: the wrapper already verified Memory
  through the authenticated installed-app inventory. A running status *is* a
  dependency. A published status whose atomic ``.ready`` pointer has not caught
  up is also still moving. Both are bounded so a wedged Memory run cannot hold
  Reflection forever.
  """
  timeout_seconds = max(0.0, timeout_seconds)
  poll_seconds = max(0.01, poll_seconds)
  started = monotonic()
  deadline = started + timeout_seconds
  last_receipt: dict[str, Any] | None = None
  while True:
    current = _read_json(memory_root / "app-state" / "run-status.json")
    if not isinstance(current, dict):
      return {
        "version": 1,
        "status": "unavailable",
        "waited_seconds": round(max(0.0, monotonic() - started), 3),
        "run": None,
      }
    receipt = _publication_receipt(memory_root, current)
    last_receipt = receipt
    if receipt["finalized"]:
      return {
        "version": 1,
        "status": "finalized",
        "waited_seconds": round(max(0.0, monotonic() - started), 3),
        "run": receipt,
      }
    if current.get("status") not in {"running", "published"}:
      return {
        "version": 1,
        "status": "unavailable",
        "waited_seconds": round(max(0.0, monotonic() - started), 3),
        "run": receipt,
      }
    now = monotonic()
    if now >= deadline:
      return {
        "version": 1,
        "status": "timeout",
        "waited_seconds": round(max(0.0, now - started), 3),
        "run": last_receipt,
      }
    sleep(min(poll_seconds, max(0.0, deadline - now)))


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
  rows.sort(key=lambda row: str(
    row.get("finished_at") or row.get("started_at") or row.get("timestamp") or ""
  ))
  return rows


def _safe_run(row: dict[str, Any] | None) -> dict[str, Any] | None:
  if not row:
    return None
  result = {
    key: row[key]
    for key in _RUN_FIELDS
    if key in row and key != "writer_self_reviews"
  }
  if "writer_self_reviews" in row:
    result["writer_self_reviews"] = _safe_self_reviews(
      row.get("writer_self_reviews"),
    )
  return result


def _safe_self_reviews(value: Any) -> list[dict[str, str]]:
  if not isinstance(value, list):
    return []
  return [
    {
      key: str(item[key])[:1000]
      for key in _SELF_REVIEW_FIELDS
      if isinstance(item.get(key), str)
    }
    for item in value[-12:]
    if isinstance(item, dict)
  ]


def _latest_update(
  app_state: Path,
  *,
  commit: str | None = None,
) -> dict[str, Any] | None:
  rows = _read_runs(app_state / "update-log")
  if commit is not None:
    rows = [row for row in rows if row.get("commit") == commit]
  if not rows:
    return None
  row = rows[-1]
  result = {
    key: row[key]
    for key in (
      "run_id", "timestamp", "commit", "provider", "model", "changed_paths",
      "deleted_paths", "topology", "model_work",
    )
    if key in row
  }
  result["writer_self_reviews"] = _safe_self_reviews(
    row.get("writer_self_reviews"),
  )
  followups = row.get("followups")
  result["followups"] = [str(item)[:1000] for item in followups[-50:]] \
    if isinstance(followups, list) else []
  return result


def _recall_hindsight(app_state: Path) -> dict[str, Any]:
  stats = _read_json(app_state / "recall-stats.json")
  if not isinstance(stats, dict):
    return {"hindsight_assessed": 0, "usefulness_counts": {}}
  counts = stats.get("usefulness_counts")
  if not isinstance(counts, dict):
    counts = {}
  return {
    "last_audited_at": stats.get("last_audited_at"),
    "reads_audited": stats.get("reads_audited"),
    "hindsight_assessed": stats.get("hindsight_assessed", 0),
    "usefulness_counts": {
      key: counts[key]
      for key in ("helpful", "mixed", "unused", "harmful", "unknown")
      if isinstance(counts.get(key), int) and not isinstance(counts.get(key), bool)
    },
  }


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
      "model_work": _model_work([]),
    }
  start_day = max(since.date(), available_days[0])
  days = []
  receipts = []
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
      traversal = row.get("traversal")
      decisions = (
        traversal.get("decisions") if isinstance(traversal, dict) else None
      )
      if isinstance(decisions, list):
        for decision in decisions:
          attempts = (
            decision.get("attempts") if isinstance(decision, dict) else None
          )
          if not isinstance(attempts, list):
            continue
          receipts.extend(
            attempt["usage_receipt"] for attempt in attempts
            if isinstance(attempt, dict)
            and isinstance(attempt.get("usage_receipt"), dict)
          )
    days.append({"date": day, "chats": len(chats)})
    cursor += dt.timedelta(days=1)
  return {
    "since": since.isoformat(),
    "through": now.isoformat(),
    "days": days,
    "chat_days": sum(day["chats"] for day in days),
    "model_work": _model_work(receipts),
  }


def _model_work(receipts: list[dict[str, Any]]) -> dict[str, Any]:
  token_usage: dict[str, int | float] = {}
  costs = []
  for receipt in receipts:
    usage = receipt.get("usage")
    if isinstance(usage, dict):
      for key, value in usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
          token_usage[str(key)] = token_usage.get(str(key), 0) + value
    cost = receipt.get("cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
      costs.append(cost)
  return {
    "attempt_count": len(receipts),
    "usage_reported_attempts": sum(
      1 for receipt in receipts if isinstance(receipt.get("usage"), dict)
    ),
    "cost_reported_attempts": len(costs),
    # A provider that omitted cost did not report a free run.
    "reported_cost_usd": sum(costs) if costs else None,
    "input_chars": sum(int(receipt.get("input_chars") or 0) for receipt in receipts),
    "output_chars": sum(int(receipt.get("output_chars") or 0) for receipt in receipts),
    "token_usage": token_usage,
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


def _graph_counts_at_revision(repository: Path, revision: str | None) -> dict[str, Any] | None:
  """Read graph.json from the immutable publication named by Memory's pointer."""
  if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40,64}", revision):
    return None
  try:
    size_result = subprocess.run(
      ["git", "-C", str(repository), "cat-file", "-s", f"{revision}:graph.json"],
      capture_output=True, text=True, timeout=10, check=False,
    )
    size = int(size_result.stdout.strip()) if size_result.returncode == 0 else -1
    if size < 0 or size > 8 * 1024 * 1024:
      return None
    result = subprocess.run(
      ["git", "-C", str(repository), "show", f"{revision}:graph.json"],
      capture_output=True, timeout=20, check=False,
    )
    if result.returncode != 0 or len(result.stdout) != size:
      return None
    value = json.loads(result.stdout.decode("utf-8"))
  except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
    return None
  if not isinstance(value, dict):
    return None
  nodes = value.get("nodes") if isinstance(value.get("nodes"), list) else []
  edges = value.get("edges") if isinstance(value.get("edges"), list) else []
  problems = value.get("problems") if isinstance(value.get("problems"), list) else []
  warnings = sum(
    1 for problem in problems
    if isinstance(problem, dict) and problem.get("severity") == "warning"
  )
  return {
    "nodes": len(nodes), "edges": len(edges), "problems": len(problems),
    "warnings": warnings, "blocking_problems": len(problems) - warnings,
  }


def build_health(
  memory_root: Path,
  *,
  now: dt.datetime | None = None,
  since: str = "1970-01-01T00:00:00Z",
  dependency: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Summarize operational state without exposing chats, facts, or note bodies."""
  now = (now or _now()).astimezone(dt.timezone.utc)
  recall_since = _parse_time(since)
  if recall_since is None:
    raise ValueError("since must be a timezone-aware ISO timestamp")
  app_state = memory_root / "app-state"
  moving_unassessed = bool(
    isinstance(dependency, dict) and dependency.get("status") == "timeout"
  )
  ready = _read_json(memory_root / ".ready")
  ready_commit = ready.get("commit") if isinstance(ready, dict) else None
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
  # A timeout deliberately limits run-level assessment to the publication that
  # Reflection can consume. Other terminal outcomes remain visible so a failed
  # writer run is diagnosed, but they must never make mutable repository bytes
  # part of the health handoff.
  if moving_unassessed:
    ready_index = next((
      index for index in range(len(terminal_runs) - 1, -1, -1)
      if terminal_runs[index].get("status") == "published"
      and terminal_runs[index].get("commit") == ready_commit
    ), None)
    assessed_runs = terminal_runs[:ready_index + 1] if ready_index is not None else []
  else:
    assessed_runs = terminal_runs
  latest_terminal = assessed_runs[-1] if assessed_runs else None
  published = [row for row in assessed_runs if row.get("status") == "published"]
  failed = [
    row for row in assessed_runs if row.get("status") in {"failed", "degraded"}
  ]
  latest_publish = published[-1] if published else None
  latest_failure = failed[-1] if failed else None

  consecutive_unsuccessful = 0
  for row in reversed(assessed_runs):
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
  ready_is_pinned = isinstance(ready_commit, str) and bool(ready_commit)
  ready_is_revision = bool(
    ready_is_pinned and re.fullmatch(r"[0-9a-f]{40,64}", ready_commit)
  )
  if ready_is_pinned:
    graph = _graph_counts_at_revision(memory_root / "repository", ready_commit)
  elif moving_unassessed or (
    isinstance(latest, dict) and latest.get("status") != "published"
  ):
    # With no publication pointer, a running/failed writer may have already
    # changed the working tree. Run metadata remains useful, but graph bytes do
    # not have an immutable identity Reflection can safely assess.
    graph = None
  else:
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
  if ready_is_revision and graph is None:
    reasons.append("ready_publication_unreadable")
  if isinstance(pending_capacity, int) and pending_count >= pending_capacity:
    reasons.append("pending_chat_queue_at_capacity")
  recall = _recall_activity(app_state, now, recall_since)
  if graph and graph["warnings"]:
    advisories.append("graph_warnings_present")
  if recovered_after_failure:
    advisories.append("recovered_after_recent_failure")
  if pending_count:
    advisories.append("pending_chat_retry_backlog")

  consumed_run = next((
    row for row in reversed(terminal_runs)
    if row.get("status") == "published"
    and ready_is_pinned
    and row.get("commit") == ready_commit
  ), None)
  consumed_publication = _publication_receipt(
    memory_root, consumed_run if ready_is_pinned else latest_terminal,
  )
  # Old Memory installations did not always retain the run receipt. The Git
  # object itself remains sufficient immutable publication evidence, but a
  # malformed/dangling .ready pointer must not be promoted to a finalized run.
  if ready_is_pinned and consumed_run is None and graph is not None:
    consumed_publication = {
      "finalized": True, "status": "published", "run_id": None,
      "started_at": None, "finished_at": None, "commit": ready_commit,
      "ready_commit": ready_commit, "publication_matches": True,
    }
  elif ready_is_revision and graph is None:
    consumed_publication["finalized"] = False

  suppress_unpinned_update = bool(
    not ready_is_pinned
    and (
      moving_unassessed
      or (isinstance(latest, dict) and latest.get("status") != "published")
    )
  )

  evidence_available = bool(
    graph or consumed_publication.get("finalized") or (
      latest if not ready_is_pinned else False
    )
  )
  return {
    "version": VERSION,
    "generated_at": now.isoformat(),
    "available": evidence_available,
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
    "recall_hindsight": _recall_hindsight(app_state),
    "latest_writer_update": None if suppress_unpinned_update else _latest_update(
      app_state, commit=ready_commit if ready_is_pinned else None,
    ),
    "consumed_publication": consumed_publication,
    "current_run_unassessed": moving_unassessed,
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
  parser.add_argument(
    "command", nargs="?", choices=("health", "await-finalized"), default="health",
  )
  parser.add_argument("--memory-root", required=True)
  parser.add_argument("--since", default="1970-01-01T00:00:00Z")
  parser.add_argument("--output", required=True)
  parser.add_argument("--timeout", type=float, default=0.0)
  parser.add_argument("--poll", type=float, default=15.0)
  parser.add_argument("--dependency", type=Path)
  args = parser.parse_args(argv)
  if args.command == "await-finalized":
    result = await_finalized_run(
      Path(args.memory_root),
      timeout_seconds=args.timeout,
      poll_seconds=args.poll,
    )
    _atomic_json(Path(args.output), result)
    return 0 if result["status"] != "timeout" else 67
  dependency = _read_json(args.dependency) if args.dependency else None
  _atomic_json(Path(args.output), build_health(
    Path(args.memory_root), since=args.since,
    dependency=dependency if isinstance(dependency, dict) else None,
  ))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
