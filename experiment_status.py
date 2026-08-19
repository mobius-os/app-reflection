#!/usr/bin/env python3
"""Summarize Reflection's append-only experiment events without judging them."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
_ACTIVE = {"proposed", "running"}
_TERMINAL = {"concluded", "abandoned"}
_FIELDS = (
  "experiment_id", "recorded_at", "area", "status", "observation",
  "hypothesis", "action", "expected_signal", "review_after",
  "review_trigger", "result",
)


def _time(value: Any) -> dt.datetime | None:
  if not isinstance(value, str):
    return None
  try:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=dt.timezone.utc)
  return parsed.astimezone(dt.timezone.utc)


def _safe(row: dict[str, Any]) -> dict[str, Any] | None:
  experiment_id = row.get("experiment_id")
  status = row.get("status")
  if not isinstance(experiment_id, str) or not _ID.fullmatch(experiment_id):
    return None
  if status not in _ACTIVE | _TERMINAL:
    return None
  result = {}
  for key in _FIELDS:
    value = row.get(key)
    if isinstance(value, str):
      result[key] = value[:2000]
  return result


def build_status(path: Path, *, now: dt.datetime | None = None) -> dict[str, Any]:
  now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
  try:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError:
    lines = []
  valid = []
  invalid = 0
  for raw in lines[-500:]:
    try:
      value = json.loads(raw)
    except ValueError:
      invalid += 1
      continue
    row = _safe(value) if isinstance(value, dict) else None
    if row is None:
      invalid += 1
    else:
      valid.append(row)
  latest = {}
  for row in valid:
    latest[row["experiment_id"]] = row
  active = sorted(
    (row for row in latest.values() if row.get("status") in _ACTIVE),
    key=lambda row: (str(row.get("review_after") or ""), row["experiment_id"]),
  )
  due = [
    row for row in active
    if (review_at := _time(row.get("review_after"))) is not None and review_at <= now
  ]
  return {
    "version": 1,
    "generated_at": now.isoformat(),
    "ledger_sha256": hashlib.sha256(
      ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    ).hexdigest(),
    "valid_event_count": len(valid),
    "invalid_event_count": invalid,
    "experiment_count": len(latest),
    "active": active,
    "due": due,
  }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
  temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  os.replace(temp, path)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--ledger", required=True)
  parser.add_argument("--output", required=True)
  args = parser.parse_args(argv)
  _atomic_json(Path(args.output), build_status(Path(args.ledger)))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
