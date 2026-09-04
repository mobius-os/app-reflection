#!/usr/bin/env python3
"""Write Reflection's model-independent failure notice after the runner exits."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any


_COPY = {
  "authentication": (
    "Reflection could not authenticate with its model provider tonight.",
    "No review cursor was advanced, so the missed interval remains queued for "
    "the next successful run.",
  ),
  "usage_limit": (
    "Reflection reached the model provider's usage limit tonight.",
    "No review cursor was advanced, so the missed interval remains queued for "
    "the next successful run.",
  ),
  "timeout": (
    "Reflection reached its wall-clock safety boundary tonight.",
    "Any completed work remains saved, but the review cursor was not advanced "
    "and the unfinished interval stays queued for a future successful run.",
  ),
  "brief_missing": (
    "Reflection finished without publishing its required morning brief.",
    "This safety notice was generated outside the agent process. The review "
    "cursor was not advanced, so the interval remains queued.",
  ),
  "runner_failed": (
    "Reflection ended before it could publish a trustworthy morning brief.",
    "Any completed work remains saved, but the review cursor was not advanced "
    "and the missed interval stays queued for a future successful run.",
  ),
}


def _atomic_text(path: Path, value: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, temporary = tempfile.mkstemp(
    dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
  )
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      handle.write(value)
      handle.flush()
      os.fsync(handle.fileno())
    os.replace(temporary, path)
  except BaseException:
    try:
      os.unlink(temporary)
    except OSError:
      pass
    raise


def _atomic_copy(source: Path, target: Path) -> None:
  target.parent.mkdir(parents=True, exist_ok=True)
  fd, temporary = tempfile.mkstemp(
    dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp",
  )
  try:
    with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
      shutil.copyfileobj(reader, writer, length=1024 * 1024)
      writer.flush()
      os.fsync(writer.fileno())
    os.replace(temporary, target)
  except BaseException:
    try:
      os.close(fd)
    except OSError:
      pass
    try:
      os.unlink(temporary)
    except OSError:
      pass
    raise


def _read_state(path: Path) -> dict:
  try:
    value = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}
  return value if isinstance(value, dict) else {}


def report_fingerprint(path: Path) -> dict[str, Any]:
  """Return a change-sensitive identity for one report, without trusting time.

  The digest proves which bytes were present while inode/change metadata also
  distinguishes a deliberate atomic re-publication of identical bytes. A
  pre-existing file with a future or same-second timestamp therefore cannot be
  mistaken for this run's deliverable.
  """
  try:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
      return {"kind": "other"}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
      while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    after = path.lstat()
  except FileNotFoundError:
    return {"kind": "missing"}
  except OSError as exc:
    return {"kind": "unreadable", "error": type(exc).__name__}
  identity = (
    before.st_dev, before.st_ino, before.st_size,
    before.st_mtime_ns, before.st_ctime_ns,
  )
  if identity != (
    after.st_dev, after.st_ino, after.st_size,
    after.st_mtime_ns, after.st_ctime_ns,
  ):
    return {"kind": "unstable"}
  return {
    "kind": "file",
    "sha256": digest.hexdigest(),
    "size": after.st_size,
    "device": after.st_dev,
    "inode": after.st_ino,
    "mtime_ns": after.st_mtime_ns,
    "ctime_ns": after.st_ctime_ns,
  }


def _backup_path(receipt_path: Path) -> Path:
  return Path(f"{receipt_path}.before")


def start_report_receipt(receipt_path: Path, report: Path, run_id: str) -> dict:
  before = report_fingerprint(report)
  backup = _backup_path(receipt_path)
  try:
    backup.unlink()
  except FileNotFoundError:
    pass
  if before.get("kind") == "file":
    _atomic_copy(report, backup)
    if report_fingerprint(report) != before:
      backup.unlink(missing_ok=True)
      raise OSError("report changed while its pre-run snapshot was captured")
    backup_fingerprint = report_fingerprint(backup)
    if (
      backup_fingerprint.get("kind") != "file"
      or backup_fingerprint.get("sha256") != before.get("sha256")
      or backup_fingerprint.get("size") != before.get("size")
    ):
      backup.unlink(missing_ok=True)
      raise OSError("pre-run report snapshot did not preserve the original bytes")
  state = _read_state(report.parent.parent / "state.json")
  receipt = {
    "schema": 1,
    "run_id": run_id,
    "report": str(report),
    "before": before,
    "backup": str(backup) if before.get("kind") == "file" else None,
    "published_this_run": False,
    "had_streak": "streak" in state,
    "streak_before": state.get("streak"),
  }
  _atomic_text(receipt_path, json.dumps(receipt, separators=(",", ":")) + "\n")
  return receipt


def check_report_receipt(receipt_path: Path, report: Path, run_id: str) -> dict:
  """Complete a pre-run receipt and prove whether this run changed the report."""
  receipt = _read_state(receipt_path)
  valid = (
    receipt.get("schema") == 1
    and receipt.get("run_id") == run_id
    and receipt.get("report") == str(report)
    and isinstance(receipt.get("before"), dict)
  )
  after = report_fingerprint(report)
  before = receipt.get("before") if valid else None
  published = bool(
    valid
    and after.get("kind") == "file"
    and (
      before.get("kind") != "file"
      or any(
        after.get(key) != before.get(key)
        for key in ("sha256", "size", "device", "inode")
      )
    )
  )
  result = {
    **receipt,
    "after": after,
    "published_this_run": published,
  } if valid else {
    "schema": 1,
    "run_id": run_id,
    "report": str(report),
    "before": None,
    "after": after,
    "published_this_run": False,
    "error": "invalid_pre_run_receipt",
  }
  _atomic_text(receipt_path, json.dumps(result, separators=(",", ":")) + "\n")
  return result


def restore_pre_run_streak(receipt_path: Path, report: Path, run_id: str) -> bool:
  receipt = _read_state(receipt_path)
  if (
    receipt.get("schema") != 1
    or receipt.get("run_id") != run_id
    or receipt.get("report") != str(report)
  ):
    return False
  state_path = report.parent.parent / "state.json"
  state = _read_state(state_path)
  if receipt.get("had_streak") is True:
    state["streak"] = receipt.get("streak_before")
  else:
    state.pop("streak", None)
  _atomic_text(
    state_path,
    json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
  )
  return True


def restore_pre_run_report(receipt_path: Path, report: Path, run_id: str) -> bool:
  """Restore a same-day report that existed before a failed retry.

  A changed fingerprint proves only that this invocation touched the report;
  it does not make bytes from a non-zero run safe to replace an earlier good
  brief. The backup path is derived locally rather than trusted from the
  receipt, and its digest must still match the captured pre-run identity.
  """
  receipt = _read_state(receipt_path)
  before = receipt.get("before")
  backup = _backup_path(receipt_path)
  if (
    receipt.get("schema") != 1
    or receipt.get("run_id") != run_id
    or receipt.get("report") != str(report)
    or not isinstance(before, dict)
    or before.get("kind") != "file"
    or receipt.get("backup") != str(backup)
  ):
    return False
  backup_fingerprint = report_fingerprint(backup)
  if (
    backup_fingerprint.get("kind") != "file"
    or backup_fingerprint.get("sha256") != before.get("sha256")
    or backup_fingerprint.get("size") != before.get("size")
  ):
    raise OSError("pre-run report snapshot is missing or corrupt")
  current = report_fingerprint(report)
  if (
    current.get("kind") == "file"
    and current.get("sha256") == before.get("sha256")
    and current.get("size") == before.get("size")
  ):
    restored_this_call = receipt.get("published_this_run") is True
  else:
    _atomic_copy(backup, report)
    restored = report_fingerprint(report)
    if (
      restored.get("kind") != "file"
      or restored.get("sha256") != before.get("sha256")
      or restored.get("size") != before.get("size")
    ):
      raise OSError("restored report does not match its pre-run identity")
    restored_this_call = True
  if not restored_this_call:
    return False
  _atomic_text(receipt_path, json.dumps({
    **receipt,
    "published_this_run": False,
    "restored_pre_run_report": True,
  }, separators=(",", ":")) + "\n")
  return True


def is_floor_brief(path: Path) -> bool:
  try:
    prefix = path.read_bytes()[:4096]
  except OSError:
    return False
  return b"data-reflection-floor=" in prefix


def _metric_day_outcomes(metrics_path: Path) -> dict[dt.date, bool]:
  outcomes: dict[dt.date, bool] = {}
  try:
    lines = metrics_path.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError:
    return outcomes
  for line in lines:
    try:
      row = json.loads(line)
      day = dt.datetime.fromisoformat(
        str(row.get("started_at")).replace("Z", "+00:00"),
      ).date()
    except (AttributeError, TypeError, ValueError):
      continue
    if not isinstance(row, dict) or row.get("dry_run") is not False:
      continue
    successful = (
      row.get("exit_code") == 0
      and row.get("brief_written") is True
      and row.get("brief_source", "agent") == "agent"
    )
    outcomes[day] = outcomes.get(day, False) or successful
  return outcomes


def substantive_streak(
  reports_dir: Path,
  run_date: dt.date,
  metrics_path: Path | None = None,
) -> int:
  """Count successful substantive Reflection days ending on ``run_date``."""
  outcomes = _metric_day_outcomes(
    metrics_path or reports_dir.parent / "reflection-run-metrics.jsonl",
  )
  streak = 1
  cursor = run_date - dt.timedelta(days=1)
  while True:
    report = reports_dir / f"{cursor.isoformat()}.html"
    if (
      not report.is_file()
      or is_floor_brief(report)
      or outcomes.get(cursor) is False
    ):
      return streak
    streak += 1
    cursor -= dt.timedelta(days=1)


def record_substantive_success(storage_dir: Path, run_date: dt.date) -> int:
  state_path = storage_dir / "state.json"
  state = _read_state(state_path)
  streak = substantive_streak(storage_dir / "reports", run_date)
  state["streak"] = streak
  _atomic_text(
    state_path,
    json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
  )
  return streak


def write_floor_brief(
  storage_dir: Path,
  *,
  run_date: dt.date,
  reason: str,
  exit_code: int,
) -> dict:
  """Atomically write the brief and merge only the failure headline into state."""
  summary, detail = _COPY[reason]
  date_text = run_date.isoformat()
  report = storage_dir / "reports" / f"{date_text}.html"
  if report.is_file() and not is_floor_brief(report):
    return {
      "brief": str(report),
      "reason": reason,
      "exit_code": exit_code,
      "state": str(storage_dir / "state.json"),
      "written": False,
      "preserved": "existing_substantive_brief",
    }
  document = f"""<!DOCTYPE html>
<html lang="en" data-reflection-floor="{html.escape(reason)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Morning brief — {date_text}</title>
</head>
<body>
  <main>
    <h1>Morning brief</h1>
    <p>{html.escape(summary)}</p>
    <p>{html.escape(detail)}</p>
  </main>
</body>
</html>
"""
  _atomic_text(report, document)

  state_path = storage_dir / "state.json"
  state = _read_state(state_path)
  state.update({
    "last_run": dt.datetime.now(dt.timezone.utc).isoformat(),
    "last_summary": summary,
    "last_outcome": {
      "status": "failed",
      "reason": reason,
      "exit_code": exit_code,
      "report": f"reports/{date_text}.html",
    },
  })
  _atomic_text(
    state_path,
    json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
  )
  return {
    "brief": str(report),
    "reason": reason,
    "exit_code": exit_code,
    "state": str(state_path),
    "written": True,
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers(dest="command")
  start = subparsers.add_parser("start-receipt")
  start.add_argument("receipt", type=Path)
  start.add_argument("report", type=Path)
  start.add_argument("run_id")
  check = subparsers.add_parser("check-receipt")
  check.add_argument("receipt", type=Path)
  check.add_argument("report", type=Path)
  check.add_argument("run_id")
  restore = subparsers.add_parser("restore-streak")
  restore.add_argument("receipt", type=Path)
  restore.add_argument("report", type=Path)
  restore.add_argument("run_id")
  restore_report = subparsers.add_parser("restore-report")
  restore_report.add_argument("receipt", type=Path)
  restore_report.add_argument("report", type=Path)
  restore_report.add_argument("run_id")
  success = subparsers.add_parser("record-success")
  success.add_argument("storage_dir", type=Path)
  success.add_argument("date")
  finalize = subparsers.add_parser("finalize-report")
  finalize.add_argument("report", type=Path)
  finalize.add_argument("date")
  parser.add_argument("--storage-dir")
  parser.add_argument("--date")
  parser.add_argument("--reason", choices=tuple(_COPY))
  parser.add_argument("--exit-code", type=int)
  args = parser.parse_args(argv)
  if args.command == "start-receipt":
    print(json.dumps(start_report_receipt(args.receipt, args.report, args.run_id)))
    return 0
  if args.command == "check-receipt":
    result = check_report_receipt(args.receipt, args.report, args.run_id)
    print("true" if result["published_this_run"] else "false")
    return 0
  if args.command == "restore-streak":
    return 0 if restore_pre_run_streak(
      args.receipt, args.report, args.run_id,
    ) else 1
  if args.command == "restore-report":
    print("true" if restore_pre_run_report(
      args.receipt, args.report, args.run_id,
    ) else "false")
    return 0
  if args.command == "record-success":
    run_date = dt.date.fromisoformat(args.date)
    print(record_substantive_success(args.storage_dir, run_date))
    return 0
  if args.command == "finalize-report":
    from reflection_runner import finalize_brief_document
    return 0 if finalize_brief_document(
      args.report, dt.date.fromisoformat(args.date),
    ) else 1
  if not all((args.storage_dir, args.date, args.reason)) or args.exit_code is None:
    parser.error("floor mode requires --storage-dir, --date, --reason, and --exit-code")
  try:
    run_date = dt.date.fromisoformat(args.date)
  except ValueError as exc:
    parser.error(str(exc))
  receipt = write_floor_brief(
    Path(args.storage_dir),
    run_date=run_date,
    reason=args.reason,
    exit_code=args.exit_code,
  )
  print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
  return 0 if receipt.get("written") is True else 2


if __name__ == "__main__":
  raise SystemExit(main())
