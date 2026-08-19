#!/usr/bin/env python3
"""Validate and summarize Reflection's agent-interview outcomes.

The ledger is a compact receipt, not a transcript and not ground truth.  It
preserves which candidates were reviewed, whether a real interview completed,
what evidence was verified, and which follow-up (if any) earned attention.
Reflection still decides whom to interview and what the evidence means.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


KINDS = {"chat", "app_run", "memory_writer"}
METHODS = {"interview", "evidence_review", "summary_sufficient", "skipped_stub"}
VERDICTS = {"verified", "contradicted", "unverified", "not_applicable"}
TEXT_FIELDS = (
  "subject_id", "subject_kind", "method", "verification", "outcome",
  "friction", "skill_signal", "memory_signal", "next_action", "reason",
)


def _text(value: Any, limit: int = 2000) -> str:
  return value.strip()[:limit] if isinstance(value, str) else ""


def normalize(row: Any) -> dict[str, Any] | None:
  if not isinstance(row, dict):
    return None
  value = {key: _text(row.get(key)) for key in TEXT_FIELDS}
  if not value["subject_id"] or value["subject_kind"] not in KINDS:
    return None
  if value["method"] not in METHODS or value["verification"] not in VERDICTS:
    return None
  if not value["outcome"]:
    return None
  evidence = row.get("evidence")
  if not isinstance(evidence, list):
    return None
  value["evidence"] = [
    _text(item, 500) for item in evidence if _text(item, 500)
  ][:20]
  if value["verification"] == "verified" and not value["evidence"]:
    return None
  return value


def _expected_ids(path: Path) -> list[str] | None:
  """Read a coverage source, distinguishing a valid empty set from failure."""
  try:
    value = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return None
  ids = value.get("subject_ids") if isinstance(value, dict) else None
  if not isinstance(ids, list):
    return None
  return list(dict.fromkeys(item for item in ids if isinstance(item, str) and item))


def build_status(path: Path, *, expected_ids: list[str] | None = None) -> dict[str, Any]:
  try:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError:
    lines = []
  valid, invalid = [], 0
  for raw in lines[-500:]:
    try:
      parsed = json.loads(raw)
    except ValueError:
      invalid += 1
      continue
    row = normalize(parsed)
    if row is None:
      invalid += 1
    else:
      valid.append(row)
  methods = Counter(row["method"] for row in valid)
  verification = Counter(row["verification"] for row in valid)
  coverage_active = expected_ids is not None
  expected = list(dict.fromkeys(expected_ids or []))
  observed = [row["subject_id"] for row in valid]
  observed_counts = Counter(observed)
  missing = [subject_id for subject_id in expected if subject_id not in observed_counts]
  unexpected = sorted(set(observed) - set(expected)) if coverage_active else []
  duplicates = sorted(
    subject_id for subject_id, count in observed_counts.items() if count > 1
  )
  followups = [
    {"subject_id": row["subject_id"], "next_action": row["next_action"]}
    for row in valid if row["next_action"]
  ]
  return {
    "version": 1,
    "source": str(path),
    "valid_outcomes": len(valid),
    "invalid_outcomes": invalid,
    "methods": dict(sorted(methods.items())),
    "verification": dict(sorted(verification.items())),
    "coverage": {
      "expected": len(expected),
      "observed": len(set(observed)),
      "complete": not missing and not unexpected and not duplicates,
      "missing_subject_ids": missing,
      "unexpected_subject_ids": unexpected,
      "duplicate_subject_ids": duplicates,
    },
    "followups": followups[:30],
    "outcomes": valid,
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
  parser.add_argument("--expected-subjects")
  args = parser.parse_args(argv)
  expected_ids = (
    _expected_ids(Path(args.expected_subjects)) if args.expected_subjects else None
  )
  result = build_status(
    Path(args.ledger),
    expected_ids=expected_ids,
  )
  result["expected_subjects_source_valid"] = (
    expected_ids is not None if args.expected_subjects else None
  )
  _atomic_json(Path(args.output), result)
  return 1 if (
    result["invalid_outcomes"]
    or (args.expected_subjects and expected_ids is None)
    or (args.expected_subjects and not result["coverage"]["complete"])
  ) else 0


if __name__ == "__main__":
  raise SystemExit(main())
