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
  friction_queue.py record <storage-dir> <proposal-id> <stage> <reference> <note>
      Append a source-referenced lifecycle observation to the existing ledger.
  friction_queue.py history <storage-dir> <proposal-id>
      Print the report-backed proposal and its observed history.
  friction_queue.py proposals <storage-dir>
      Print the current report-backed proposal view, preserving unknown stages.
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


# Observations, never inferred authority or an automatic state machine.
PROPOSAL_STAGES = {"revised", "approved", "declined", "implemented", "activated", "verified"}


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
    if outcome.get("kind") != "proposal_event"
    and (not outcome.get("run") or (storage / "reports" / f"{outcome['run']}.md").is_file())
  }
  return [
    entry for entry in _read_jsonl(storage / "friction.jsonl")
    if isinstance(entry.get("id"), str) and entry["id"] not in settled
  ]


def _proposal_trails(storage: Path) -> dict[str, list[dict]]:
  """Project the existing ledger once; absence of evidence stays unknown."""
  records = _read_jsonl(storage / "outcomes.jsonl")
  backed = [record for record in records
            if record.get("kind") == "proposal_event" or
            (record.get("run") and
             (storage / "reports" / f"{record['run']}.md").is_file())]
  trails: dict[str, list[dict]] = {}
  for record in backed:
    proposal_id = record.get("friction_id")
    if isinstance(proposal_id, str) and record.get("outcome") == "asked":
      trails.setdefault(proposal_id, [record])
  for record in backed:
    proposal_id = record.get("proposal_id")
    if (isinstance(proposal_id, str) and proposal_id in trails and
        (record.get("kind") == "proposal_event" or
         (record.get("outcome") == "joined" and isinstance(record.get("friction_id"), str)))):
      trails[proposal_id].append(record)
  return trails


def history(storage: Path, proposal_id: str) -> list[dict]:
  return _proposal_trails(storage).get(proposal_id, [])


def proposals(storage: Path) -> list[dict]:
  """Expose observed stages, not an inferred approval or completion status."""
  view = []
  for proposal_id, trail in _proposal_trails(storage).items():
    origin = trail[0]
    events = [record for record in trail[1:] if record.get("kind") == "proposal_event"]
    last = events[-1] if events else None
    view.append({"proposal_id": proposal_id, "report_run": origin.get("run"),
                 "proposal_note": origin.get("note"),
                 "linked_friction_ids": [record["friction_id"] for record in trail[1:]
                                         if record.get("outcome") == "joined"],
                 "last_observation": last,
                 "open_for_review": (last is None or trail[-1].get("outcome") == "joined"
                                     or last.get("stage") not in ("declined", "verified"))})
  return view


def record_proposal(storage: Path, proposal_id: str, stage: str,
                    reference: str, note: str) -> dict:
  if not proposal_id or stage not in PROPOSAL_STAGES:
    raise ValueError("an asked friction ID and valid proposal stage are required")
  if not reference.strip() or not note.strip():
    raise ValueError("a source reference and evidence note are required")
  with (storage / "outcomes.jsonl").open("a+b") as outcomes:
    fcntl.flock(outcomes, fcntl.LOCK_EX)
    trail = history(storage, proposal_id)
    if not trail:
      raise ValueError("proposal needs a report-backed asked outcome")
    for existing in trail[1:]:
      if (existing.get("kind") == "proposal_event" and existing.get("stage") == stage
          and existing.get("reference") == reference.strip()):
        return existing  # Re-observing the same source is not a new decision.
    event = {"kind": "proposal_event", "proposal_id": proposal_id,
             "stage": stage, "reference": reference.strip(), "note": note.strip(),
             "at": datetime.now(UTC).isoformat(timespec="seconds")}
    outcomes.seek(0, os.SEEK_END)
    if outcomes.tell():
      outcomes.seek(-1, os.SEEK_END)
      if outcomes.read(1) != b"\n":
        outcomes.write(b"\n")
    outcomes.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
    outcomes.flush()
    os.fsync(outcomes.fileno())
    return event


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
    if "proposal_id" in item and (item["outcome"] != "joined" or
                                  not isinstance(item["proposal_id"], str) or not item["proposal_id"]):
      raise ValueError("proposal_id is only valid for a joined entry")

  with (storage / "outcomes.jsonl").open("a+b") as outcomes:
    fcntl.flock(outcomes, fcntl.LOCK_EX)
    if any(record.get("run") == run for record in _read_jsonl(storage / "outcomes.jsonl")):
      raise ValueError("this run already published outcomes; its report cannot be replaced")
    remaining = {entry["id"] for entry in pending(storage)}
    if any(friction_id not in remaining for friction_id in ids):
      raise ValueError("an entry was settled by another run; refresh the report and retry")
    joined = [item["proposal_id"] for item in decisions if "proposal_id" in item]
    if joined and not set(joined) <= _proposal_trails(storage).keys():
      raise ValueError("joined proposal_id needs a report-backed asked outcome")
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
                "note": item["note"].strip(), "at": at, "run": run,
                **({"proposal_id": item["proposal_id"]} if "proposal_id" in item else {})}
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
  if len(argv) == 7 and argv[1] == "record":
    try:
      print(json.dumps(record_proposal(Path(argv[2]), *argv[3:]), ensure_ascii=False))
    except (OSError, ValueError) as exc:
      print(f"friction_queue: {exc}", file=sys.stderr)
      return 1
    return 0
  if len(argv) == 4 and argv[1] == "history":
    print(json.dumps(history(Path(argv[2]), argv[3]), ensure_ascii=False, indent=1))
    return 0
  if len(argv) == 3 and argv[1] == "proposals":
    print(json.dumps(proposals(Path(argv[2])), ensure_ascii=False, indent=1))
    return 0
  print(__doc__.strip(), file=sys.stderr)
  return 2


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
