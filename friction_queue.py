#!/usr/bin/env python3
"""The friction backlog: logged entries that still need an outcome.

``friction.jsonl`` (written by the log_friction agent tool) is the queue;
``outcomes.jsonl`` records each settled entry's outcome. A run's outcome only
counts after its report exists. This lets a later run recover entries left by
an interrupted report handoff.

  friction_queue.py pending <storage-dir>
      Print the pending entries as a JSON array, oldest first.
  friction_queue.py publish <storage-dir> <draft-file> <outcomes-file>
      Publish this run's report and the reported outcomes together.
"""

from __future__ import annotations

from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile

OUTCOMES = {
  # Worth a fix or an experiment; proposed in the report, not approved.
  "asked": "a fix or experiment was proposed to the owner",
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
    if not outcome.get("run") or (storage / "reports" / f"{outcome['run']}.md").is_file()
  }
  return [
    entry for entry in _read_jsonl(storage / "friction.jsonl")
    if isinstance(entry.get("id"), str) and entry["id"] not in settled
  ]


def publish(storage: Path, draft: Path, decisions: list[dict]) -> Path:
  run = os.environ.get("CHAT_ID")
  if not run:
    raise ValueError("CHAT_ID is required to publish a run report")
  content = draft.read_bytes()
  if not content.strip():
    raise ValueError("report is empty")
  if not isinstance(decisions, list) or not decisions:
    raise ValueError("outcomes must be a nonempty JSON array")
  ids = [item.get("friction_id") for item in decisions if isinstance(item, dict)]
  if (len(ids) != len(decisions) or any(not isinstance(value, str) or not value for value in ids)
      or len(set(ids)) != len(ids)):
    raise ValueError("each outcome needs a distinct friction_id")
  for item in decisions:
    if item.get("outcome") not in OUTCOMES:
      raise ValueError(f"outcome must be one of: {', '.join(OUTCOMES)}")
    if not isinstance(item.get("note"), str) or not item["note"].strip():
      raise ValueError("each note must say what was concluded")

  with (storage / "outcomes.jsonl").open("a+b") as outcomes:
    fcntl.flock(outcomes, fcntl.LOCK_EX)
    if any(record.get("run") == run for record in _read_jsonl(storage / "outcomes.jsonl")):
      raise ValueError("this run already published outcomes; its report cannot be replaced")
    remaining = {entry["id"] for entry in pending(storage)}
    if any(friction_id not in remaining for friction_id in ids):
      raise ValueError("an entry was settled by another run; refresh the report and retry")
    reports = storage / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{run}.", dir=reports)
    try:
      with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
      destination = reports / f"{run}.md"
      os.replace(temporary, destination)
      directory = os.open(reports, os.O_RDONLY)
      try:
        os.fsync(directory)
      finally:
        os.close(directory)
    finally:
      if os.path.exists(temporary):
        os.unlink(temporary)
    at = datetime.now(UTC).isoformat(timespec="seconds")
    records = [{"friction_id": item["friction_id"], "outcome": item["outcome"],
                "note": item["note"].strip(), "at": at, "run": run}
               for item in decisions]
    outcomes.seek(0, os.SEEK_END)
    if outcomes.tell():
      outcomes.seek(-1, os.SEEK_END)
      if outcomes.read(1) != b"\n":
        outcomes.write(b"\n")  # Keep a crashed partial line from swallowing the next record.
    payload = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    outcomes.write(payload.encode("utf-8"))
    outcomes.flush()
    os.fsync(outcomes.fileno())
    return destination


def main(argv: list[str]) -> int:
  if len(argv) == 3 and argv[1] == "pending":
    print(json.dumps(pending(Path(argv[2])), ensure_ascii=False, indent=1))
    return 0
  if len(argv) == 5 and argv[1] == "publish":
    try:
      decisions = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
      print(publish(Path(argv[2]), Path(argv[3]), decisions))
    except (OSError, ValueError) as exc:
      print(f"friction_queue: {exc}", file=sys.stderr)
      return 1
    return 0
  print(__doc__.strip(), file=sys.stderr)
  return 2


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
