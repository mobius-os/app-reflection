#!/usr/bin/env python3
"""The friction backlog: logged entries that still need an outcome.

``friction.jsonl`` (written by the log_friction agent tool) is the queue;
``outcomes.jsonl`` records each settled entry's outcome. An entry is
pending until it has an outcome, so a missed, failed, or interrupted run
never skips anything: the next run starts from whatever is still pending,
oldest first.

  friction_queue.py pending <storage-dir>
      Print the pending entries as a JSON array, oldest first.
  friction_queue.py settle <storage-dir> <friction-id> <outcome> <note>
      Record one entry's outcome. <outcome> is one of OUTCOMES.
"""

from __future__ import annotations

from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import sys

OUTCOMES = {
  # Worth a fix or an experiment; the owner is asked before any work starts.
  "asked": "the owner was asked whether to fix it",
  # Same cause as another entry; the note names it.
  "joined": "same cause as another entry",
  # Already fixed since it was logged; the note says by what.
  "resolved": "already fixed since it was logged",
  # Understood, and nothing is worth changing; the note says why.
  "explained": "understood, no change worth making",
}


def _read_jsonl(path: Path) -> list[dict]:
  try:
    lines = path.read_text(encoding="utf-8").splitlines()
  except FileNotFoundError:
    return []
  entries = []
  for line in lines:
    try:
      value = json.loads(line)
    except ValueError:
      continue  # A line still being appended; it completes on the next read.
    if isinstance(value, dict):
      entries.append(value)
  return entries


def pending(storage: Path) -> list[dict]:
  settled = {
    outcome.get("friction_id")
    for outcome in _read_jsonl(storage / "outcomes.jsonl")
  }
  return [
    entry for entry in _read_jsonl(storage / "friction.jsonl")
    if isinstance(entry.get("id"), str) and entry["id"] not in settled
  ]


def settle(storage: Path, friction_id: str, outcome: str, note: str) -> dict:
  if outcome not in OUTCOMES:
    raise ValueError(f"outcome must be one of: {', '.join(OUTCOMES)}")
  if not note.strip():
    raise ValueError("note must say what was concluded")
  if friction_id not in {entry["id"] for entry in pending(storage)}:
    raise ValueError(f"no pending friction entry {friction_id}")
  record = {
    "friction_id": friction_id,
    "outcome": outcome,
    "note": note.strip(),
    "at": datetime.now(UTC).isoformat(timespec="seconds"),
  }
  # The Reflection chat settling it, so the app can tell what still waits on
  # the owner's answer in that chat.
  if os.environ.get("CHAT_ID"):
    record["run"] = os.environ["CHAT_ID"]
  with (storage / "outcomes.jsonl").open("a", encoding="utf-8") as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
  return record


def main(argv: list[str]) -> int:
  if len(argv) == 3 and argv[1] == "pending":
    print(json.dumps(pending(Path(argv[2])), ensure_ascii=False, indent=1))
    return 0
  if len(argv) == 6 and argv[1] == "settle":
    try:
      record = settle(Path(argv[2]), argv[3], argv[4], argv[5])
    except ValueError as exc:
      print(f"friction_queue: {exc}", file=sys.stderr)
      return 1
    print(json.dumps(record, ensure_ascii=False))
    return 0
  print(__doc__.strip(), file=sys.stderr)
  return 2


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
