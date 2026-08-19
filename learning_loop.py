#!/usr/bin/env python3
"""Compose one bounded orientation view over Reflection's evidence bundle.

This deliberately does not score, rank, or diagnose.  Metrics remain evidence;
the agent follows the named source files whenever a signal may affect judgment.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def _json(path: Path) -> Any:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return None


def _pick(value: Any, *keys: str) -> Any:
  for key in keys:
    value = value.get(key) if isinstance(value, dict) else None
  return value


def build(inputs: Path) -> dict[str, Any]:
  memory = _json(inputs / "memory-health.json") or {}
  friction = _json(inputs / "tool-friction.json") or {}
  experiments = _json(inputs / "experiment-status.json") or {}
  activity = _json(inputs / "activity-status.json") or {}
  chats = _json(inputs / "chats-status.json") or {}
  resources = _json(inputs / "resource-snapshot.json") or {}
  effort = _json(inputs / "latest-effort.json") or {}
  effort_summary = _json(inputs / "effort-summary.json") or {}
  model_usage = effort.get("model_usage") if isinstance(effort.get("model_usage"), dict) else {}
  attempts = model_usage.get("attempts") if isinstance(model_usage.get("attempts"), list) else []
  token_totals: dict[str, int | float] = {}
  for attempt in attempts:
    usage = attempt.get("usage") if isinstance(attempt, dict) else None
    if not isinstance(usage, dict):
      continue
    for key, value in usage.items():
      if isinstance(value, (int, float)) and not isinstance(value, bool):
        token_totals[key] = token_totals.get(key, 0) + value
  return {
    "version": 1,
    "purpose": (
      "Orientation only. These signals are evidence for intelligent judgment, "
      "not objectives or a composite score. Open the named source before acting."
    ),
    "sources": {
      "activity": "activity-status.json",
      "chats": "chats-status.json and chats.md",
      "memory": "memory-health.json",
      "friction": "tool-friction.json",
      "experiments": "experiment-status.json and experiments.jsonl",
      "resources": "resource-snapshot.json and resource-history.jsonl",
      "prior_effort": "latest-effort.json and reflection-run-history.txt",
      "rolling_effort": "effort-summary.json",
    },
    "collection_health": {
      "activity_ok": activity.get("ok"),
      "activity_events": activity.get("event_count"),
      "active_chats_ok": chats.get("active_ok"),
      "deleted_chats_complete": chats.get("deleted_complete"),
    },
    "learning_state": {
      "memory_needs_attention": memory.get("needs_attention"),
      "memory_reasons": memory.get("reasons", []),
      "recall_chats": _pick(memory, "recall_activity", "chat_days"),
      "recall_hindsight": memory.get("recall_hindsight"),
      "active_experiments": len(experiments.get("active", [])),
      "due_experiments": len(experiments.get("due", [])),
    },
    "friction_state": {
      "failure_families": friction.get("failure_families", []),
      "avoidable_call_candidates": friction.get("avoidable_call_candidates", {}),
    },
    "resource_state": {
      "pressure": resources.get("pressure"),
      "trend": resources.get("trend", {}),
    },
    "prior_effort": {
      "exit_code": effort.get("exit_code"),
      "duration_seconds": effort.get("duration_seconds"),
      "reported_cost_usd": model_usage.get("reported_cost_usd"),
      "token_usage": token_totals,
      "average_token_ratio": effort_summary.get("average_token_ratio"),
      "average_token_ratio_runs": effort_summary.get("window_runs"),
    },
  }


def _atomic(path: Path, value: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
  temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  os.replace(temp, path)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--inputs", required=True)
  parser.add_argument("--output", required=True)
  args = parser.parse_args(argv)
  _atomic(Path(args.output), build(Path(args.inputs)))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
