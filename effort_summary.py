#!/usr/bin/env python3
"""Build the one rolling effort signal the owner asked to see in reports.

The ratio is descriptive evidence, never a target: total reported Reflection
tokens divided by total same-window foreground-chat tokens across recent
successful comparable runs. Aggregating totals (rather than averaging daily
percentages) avoids giving a quiet day the same weight as a busy one.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


TOKEN_KEYS = {
  "input_tokens", "output_tokens", "cached_input_tokens",
  "cache_read_input_tokens", "cache_creation_input_tokens",
}


def _number(value: Any) -> float | None:
  return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _attempt_tokens(attempt: Any) -> float | None:
  usage = attempt.get("usage") if isinstance(attempt, dict) else None
  if not isinstance(usage, dict):
    return None
  total = _number(usage.get("total_tokens"))
  if total is not None:
    return total
  values = [_number(usage.get(key)) for key in TOKEN_KEYS]
  reported = [value for value in values if value is not None]
  return sum(reported) if reported else None


def build(path: Path, *, limit: int = 7) -> dict[str, Any]:
  try:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError:
    lines = []
  comparable = []
  for raw in reversed(lines):
    try:
      row = json.loads(raw)
    except ValueError:
      continue
    if not isinstance(row, dict) or row.get("dry_run") or row.get("exit_code") != 0:
      continue
    model = row.get("model_usage")
    if not isinstance(model, dict):
      continue
    attempts = model.get("attempts")
    chat = (model.get("work_context") or {}).get("chat_agent_work")
    chat_tokens = _number(chat.get("total_tokens")) if isinstance(chat, dict) else None
    if not isinstance(attempts, list) or chat_tokens is None or chat_tokens <= 0:
      continue
    values = [_attempt_tokens(attempt) for attempt in attempts]
    reflection_tokens = sum(value for value in values if value is not None)
    if not any(value is not None for value in values):
      continue
    comparable.append({
      "started_at": row.get("started_at"),
      "reflection_tokens": int(reflection_tokens),
      "foreground_tokens": int(chat_tokens),
    })
    if len(comparable) >= max(1, limit):
      break
  comparable.reverse()
  reflection_total = sum(row["reflection_tokens"] for row in comparable)
  foreground_total = sum(row["foreground_tokens"] for row in comparable)
  return {
    "version": 1,
    "definition": (
      "sum of all reported Reflection input, cache-read, cache-creation, and "
      "output tokens divided by same-window foreground-chat total tokens"
    ),
    "window_runs": len(comparable),
    "max_window_runs": max(1, limit),
    "reflection_tokens": reflection_total,
    "foreground_tokens": foreground_total,
    "average_token_ratio": (
      reflection_total / foreground_total if foreground_total else None
    ),
    "runs": comparable,
  }


def _atomic(path: Path, value: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
  temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  os.replace(temp, path)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--metrics", required=True)
  parser.add_argument("--output", required=True)
  parser.add_argument("--limit", type=int, default=7)
  args = parser.parse_args(argv)
  _atomic(Path(args.output), build(Path(args.metrics), limit=args.limit))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
