#!/usr/bin/env python3
"""Bounded contribution-worktree housekeeping for Reflection.

The nightly agent should spend its turns on judgment, not repeatable Git
bookkeeping. This helper runs before the agent and owns only proofs that are
deterministic:

* an exact reviewed head is recorded as merged with a public URL.

It also classifies whether an unreferenced platform worktree has any patch
absent from upstream, but that is evidence for Reflection rather than deletion
authority: merge topology and worktree intent can still be ambiguous. Even an
exact merged record is removed only when its checkout is clean, inactive,
stable, and linked. Prepared or open records, dirty work, standalone clones,
and ambiguous histories are reported rather than guessed at.

Dry-run is the default. ``--apply`` enables the narrowly proven removals.
The structured JSON output is the handoff to the nightly agent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Iterable

VERSION = 1
ACTIONABLE_STATUSES = {"prepared", "draft", "open", "submitting"}
MAX_EXCEPTIONS = 100


def _run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
  try:
    return subprocess.run(
      args,
      text=True,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      timeout=timeout,
      check=False,
    )
  except (OSError, subprocess.TimeoutExpired) as exc:
    return subprocess.CompletedProcess(args, 127, "", str(exc))


def _atomic_json(path: Path, payload: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, raw_tmp = tempfile.mkstemp(
    prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
  )
  tmp = Path(raw_tmp)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
      handle.write("\n")
      handle.flush()
      os.fsync(handle.fileno())
    os.replace(tmp, path)
  finally:
    try:
      tmp.unlink()
    except FileNotFoundError:
      pass


def _within(path: Path, parent: Path) -> bool:
  try:
    path.relative_to(parent)
    return True
  except ValueError:
    return False


def _discover_contributions_dir(
  data_dir: Path,
  api_base_url: str,
  token_file: Path,
) -> tuple[Path | None, str | None]:
  try:
    token = token_file.read_text(encoding="utf-8").strip()
  except OSError as exc:
    return None, f"token-unreadable: {exc}"
  request = urllib.request.Request(
    f"{api_base_url.rstrip('/')}/api/apps/",
    headers={"Authorization": f"Bearer {token}"},
  )
  try:
    with urllib.request.urlopen(request, timeout=20) as response:
      apps = json.load(response)
  except Exception as exc:
    return None, f"apps-api-unavailable: {exc}"
  if isinstance(apps, dict):
    apps = apps.get("apps", [])
  for app in apps if isinstance(apps, list) else []:
    if not isinstance(app, dict):
      continue
    if (app.get("slug") or app.get("name")) != "contribute":
      continue
    app_id = str(app.get("id") or "")
    if app_id.isascii() and app_id.isdecimal() and not app_id.startswith("0"):
      return data_dir / "apps" / app_id / "contributions", None
  return None, "contribute-not-installed"


def _load_records(contributions_dir: Path) -> tuple[list[dict], list[str]]:
  records: list[dict] = []
  errors: list[str] = []
  try:
    paths = sorted(contributions_dir.glob("*.json"))
  except OSError as exc:
    return [], [f"ledger-list-failed: {exc}"]
  for path in paths:
    try:
      value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
      errors.append(f"{path.name}: invalid-record: {exc}")
      continue
    if not isinstance(value, dict):
      errors.append(f"{path.name}: record-not-object")
      continue
    plan = value.get("plan") if isinstance(value.get("plan"), dict) else {}
    records.append({
      "id": str(value.get("id") or path.stem),
      "status": str(value.get("status") or "missing"),
      "url": value.get("url") if isinstance(value.get("url"), str) else None,
      "repo_path": plan.get("repo_path") if isinstance(plan.get("repo_path"), str) else None,
      "head_sha": plan.get("head_sha") if isinstance(plan.get("head_sha"), str) else None,
      "branch": (
        value.get("branch") if isinstance(value.get("branch"), str)
        else plan.get("branch") if isinstance(plan.get("branch"), str)
        else None
      ),
    })
  return records, errors


def _active_cwds() -> set[Path]:
  result: set[Path] = set()
  proc = Path("/proc")
  try:
    entries = list(proc.iterdir())
  except OSError:
    return result
  for entry in entries:
    if not entry.name.isdecimal():
      continue
    try:
      result.add(Path(os.path.realpath(entry / "cwd")))
    except OSError:
      continue
  return result


def _is_active(path: Path, active_cwds: Iterable[Path]) -> bool:
  return any(cwd == path or _within(cwd, path) for cwd in active_cwds)


def _parse_worktrees(repo: Path) -> tuple[list[dict], str | None]:
  result = _run("git", "-C", str(repo), "worktree", "list", "--porcelain")
  if result.returncode:
    return [], f"worktree-list-failed: {result.stderr.strip()[:300]}"
  rows: list[dict] = []
  for block in result.stdout.strip().split("\n\n"):
    if not block.strip():
      continue
    row: dict[str, str | bool] = {}
    for line in block.splitlines():
      key, _, value = line.partition(" ")
      row[key] = value if value else True
    rows.append(row)
  return rows, None


def _owner_for_linked_worktree(
  path: Path,
  data_dir: Path,
) -> tuple[Path | None, str | None]:
  common = _run(
    "git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir",
  )
  if common.returncode:
    return None, "not-a-git-worktree"
  common_dir = Path(common.stdout.strip()).resolve()
  if common_dir == (path / ".git").resolve():
    return None, "standalone-checkout"
  if common_dir.name != ".git":
    return None, "unexpected-git-common-dir"
  owner = common_dir.parent
  allowed = (
    owner == data_dir
    or owner == data_dir / "platform"
    or (_within(owner, data_dir / "apps") and owner.parent == data_dir / "apps")
  )
  if not allowed:
    return None, "owner-outside-managed-roots"
  return owner, None


def _inspect_worktree(
  path: Path,
  data_dir: Path,
  active_cwds: set[Path],
) -> dict:
  item = {
    "path": str(path),
    "head_sha": None,
    "branch": None,
    "owner": None,
    "reasons": [],
  }
  if not path.exists():
    item["reasons"].append("missing")
    return item
  resolved = path.resolve()
  if not _within(resolved, (data_dir / "contrib").resolve()):
    item["reasons"].append("path-outside-contrib")
    return item
  if _is_active(resolved, active_cwds):
    item["reasons"].append("active-process")
  status = _run("git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=normal")
  if status.returncode:
    item["reasons"].append("status-unavailable")
  elif status.stdout.strip():
    item["reasons"].append("dirty")
  head = _run("git", "-C", str(path), "rev-parse", "HEAD")
  if head.returncode:
    item["reasons"].append("head-unavailable")
  else:
    item["head_sha"] = head.stdout.strip()
  branch = _run("git", "-C", str(path), "symbolic-ref", "--quiet", "--short", "HEAD")
  if branch.returncode == 0:
    item["branch"] = branch.stdout.strip()
  owner, owner_error = _owner_for_linked_worktree(path, data_dir)
  if owner_error:
    item["reasons"].append(owner_error)
  elif owner is not None:
    item["owner"] = str(owner)
  return item


def _all_patches_upstream(path: Path, upstream_ref: str) -> tuple[bool, str | None]:
  result = _run("git", "-C", str(path), "cherry", upstream_ref, "HEAD")
  if result.returncode:
    return False, f"upstream-check-failed: {result.stderr.strip()[:240]}"
  if any(line.startswith("+") for line in result.stdout.splitlines()):
    return False, "patches-not-upstream"
  return True, None


def _directory_bytes(path: Path) -> int:
  result = _run("du", "-s", "-B1", str(path), timeout=60)
  if result.returncode or not result.stdout.strip():
    return 0
  try:
    return int(result.stdout.split()[0])
  except (IndexError, ValueError):
    return 0


def _records_for(
  records: Iterable[dict],
  path: Path,
  branch: str | None,
) -> list[dict]:
  resolved = path.resolve()
  matched = []
  for record in records:
    raw_path = record.get("repo_path")
    same_path = False
    if isinstance(raw_path, str):
      try:
        same_path = Path(raw_path).resolve() == resolved
      except OSError:
        same_path = False
    # A pathful record belongs to that exact checkout. Branch-only matching is
    # retained solely for old records that predate repo_path; otherwise a reused
    # branch name in another repository could incorrectly supply proof.
    same_legacy_branch = (
      not raw_path
      and branch
      and record.get("branch") == branch
    )
    if same_path or same_legacy_branch:
      matched.append(record)
  return matched


def _remove_candidate(
  candidate: dict,
  *,
  data_dir: Path,
  contributions_dir: Path,
) -> tuple[dict | None, dict | None]:
  """Revalidate and remove one candidate, returning (cleaned, exception)."""
  path = Path(candidate["path"])
  records, errors = _load_records(contributions_dir)
  active = _active_cwds()
  current = _inspect_worktree(path, data_dir, active)
  if current["head_sha"] != candidate["head_sha"]:
    current["reasons"].append("head-changed")
  matched = _records_for(records, path, current.get("branch") or candidate.get("branch"))
  if any(record["status"] in ACTIONABLE_STATUSES for record in matched):
    current["reasons"].append("actionable-record")
  proof = candidate["proof"]
  if proof == "exact-merged-record":
    exact = any(
      record["status"] == "merged"
      and record["url"]
      and record["head_sha"] == current["head_sha"]
      for record in matched
    )
    if not exact:
      current["reasons"].append("merged-proof-changed")
  if errors:
    current["reasons"].append("ledger-became-unreadable")
  if current["reasons"]:
    return None, current
  owner = Path(str(current["owner"]))
  before_bytes = _directory_bytes(path)
  removal = _run("git", "-C", str(owner), "worktree", "remove", str(path), timeout=120)
  if removal.returncode:
    current["reasons"].append(f"remove-failed: {removal.stderr.strip()[:240]}")
    return None, current
  branch = current.get("branch")
  branch_removed = False
  if branch:
    branch_head = _run("git", "-C", str(owner), "rev-parse", f"refs/heads/{branch}")
    if branch_head.returncode == 0 and branch_head.stdout.strip() == candidate["head_sha"]:
      deleted = _run("git", "-C", str(owner), "branch", "-D", branch)
      branch_removed = deleted.returncode == 0
  parent = path.parent
  if parent != data_dir / "contrib":
    try:
      parent.rmdir()
    except OSError:
      pass
  return {
    "path": str(path),
    "head_sha": candidate["head_sha"],
    "proof": proof,
    "branch_removed": branch_removed,
    "estimated_bytes": before_bytes,
    "record_ids": [record["id"] for record in matched if record["status"] == "merged"],
  }, None


def run_housekeeping(
  *,
  data_dir: Path,
  contributions_dir: Path,
  output: Path,
  apply: bool,
  upstream_ref: str = "origin/main",
  active_cwds: set[Path] | None = None,
) -> dict:
  started = dt.datetime.now(dt.timezone.utc)
  contrib_root = (data_dir / "contrib").resolve()
  records, ledger_errors = _load_records(contributions_dir)
  active = active_cwds if active_cwds is not None else _active_cwds()
  disk_before = shutil.disk_usage(data_dir).used
  preserved = Counter()
  candidates: dict[Path, dict] = {}
  exceptions: list[dict] = []
  referenced_paths: set[Path] = set()

  for record in records:
    raw_path = record.get("repo_path")
    if not raw_path:
      preserved["record-without-checkout"] += 1
      continue
    path = Path(raw_path)
    try:
      resolved = path.resolve()
    except OSError:
      preserved["invalid-checkout-path"] += 1
      continue
    if not _within(resolved, contrib_root):
      preserved["checkout-outside-contrib"] += 1
      continue
    referenced_paths.add(resolved)

  for path in sorted(referenced_paths):
    if not path.exists():
      preserved["referenced-checkout-missing"] += 1
      continue
    inspected = _inspect_worktree(path, data_dir, active)
    branch = inspected.get("branch")
    matched = _records_for(records, path, branch)
    statuses = {record["status"] for record in matched}
    inspected["record_ids"] = [record["id"] for record in matched]
    inspected["statuses"] = sorted(statuses)
    if statuses & ACTIONABLE_STATUSES:
      preserved["actionable-checkout"] += 1
      continue
    exact = any(
      record["status"] == "merged"
      and record["url"]
      and record["head_sha"] == inspected["head_sha"]
      for record in matched
    )
    if exact and not inspected["reasons"]:
      candidates[path] = {
        **inspected,
        "proof": "exact-merged-record",
      }
    elif "merged" in statuses or statuses & {"closed", "abandoned"}:
      if not exact:
        inspected["reasons"].append("no-exact-merged-head")
      exceptions.append(inspected)
    else:
      preserved["nonterminal-checkout"] += 1

  platform = data_dir / "platform"
  platform_rows, platform_error = _parse_worktrees(platform)
  if platform_error:
    ledger_errors.append(platform_error)
  missing_registrations = 0
  for row in platform_rows:
    raw_path = row.get("worktree")
    if not isinstance(raw_path, str) or not raw_path.startswith(str(data_dir / "contrib") + "/"):
      continue
    path = Path(raw_path)
    try:
      resolved = path.resolve()
    except OSError:
      continue
    if not path.exists():
      missing_registrations += 1
      continue
    if resolved in referenced_paths or path in candidates:
      continue
    inspected = _inspect_worktree(path, data_dir, active)
    inspected["record_ids"] = []
    inspected["statuses"] = []
    if row.get("locked"):
      inspected["reasons"].append("locked")
    represented, reason = _all_patches_upstream(path, upstream_ref)
    if represented:
      inspected["reasons"].append("all-patches-upstream-unreferenced")
    elif reason:
      inspected["reasons"].append(reason)
    exceptions.append(inspected)

  cleaned: list[dict] = []
  if apply:
    for candidate in sorted(candidates.values(), key=lambda item: item["path"]):
      item, exception = _remove_candidate(
        candidate,
        data_dir=data_dir,
        contributions_dir=contributions_dir,
      )
      if item:
        cleaned.append(item)
      elif exception:
        exceptions.append(exception)

  pruned_registrations = 0
  if apply and missing_registrations:
    before_rows, _ = _parse_worktrees(platform)
    _run("git", "-C", str(platform), "worktree", "prune", "--verbose", timeout=120)
    after_rows, _ = _parse_worktrees(platform)
    pruned_registrations = max(0, len(before_rows) - len(after_rows))

  empty_dirs_removed = 0
  if apply:
    try:
      top_dirs = list((data_dir / "contrib").iterdir())
    except OSError:
      top_dirs = []
    for path in top_dirs:
      if not path.is_dir():
        continue
      try:
        path.rmdir()
      except OSError:
        continue
      empty_dirs_removed += 1

  disk_after = shutil.disk_usage(data_dir).used
  status = "ok"
  if ledger_errors:
    status = "partial"
  payload = {
    "version": VERSION,
    "status": status,
    "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "duration_ms": round(
      (dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000,
    ),
    "applied": apply,
    "source": {
      "contributions_dir": str(contributions_dir),
      "records_read": len(records),
      "errors": ledger_errors[:20],
    },
    "summary": {
      "candidate_count": len(candidates),
      "cleaned_count": len(cleaned),
      "missing_registrations_seen": missing_registrations,
      "pruned_registrations": pruned_registrations,
      "empty_directories_removed": empty_dirs_removed,
      "exceptions_count": len(exceptions),
      "preserved": dict(sorted(preserved.items())),
      "bytes_reclaimed": max(0, disk_before - disk_after) if apply else 0,
    },
    "cleaned": cleaned,
    "would_clean": [] if apply else list(candidates.values()),
    "needs_reasoning": exceptions[:MAX_EXCEPTIONS],
    "needs_reasoning_omitted": max(0, len(exceptions) - MAX_EXCEPTIONS),
  }
  _atomic_json(output, payload)
  return payload


def _unavailable_payload(output: Path, reason: str, apply: bool) -> dict:
  payload = {
    "version": VERSION,
    "status": "unavailable",
    "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "applied": apply,
    "source": {"error": reason},
    "summary": {
      "candidate_count": 0,
      "cleaned_count": 0,
      "exceptions_count": 0,
      "bytes_reclaimed": 0,
    },
    "cleaned": [],
    "would_clean": [],
    "needs_reasoning": [],
    "needs_reasoning_omitted": 0,
  }
  _atomic_json(output, payload)
  return payload


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--data-dir", type=Path, default=Path("/data"))
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--contributions-dir", type=Path)
  parser.add_argument("--api-base-url", default="http://localhost:8000")
  parser.add_argument("--token-file", type=Path)
  parser.add_argument("--upstream-ref", default="origin/main")
  parser.add_argument("--apply", action="store_true")
  args = parser.parse_args()
  data_dir = args.data_dir.resolve()
  token_file = args.token_file or data_dir / "service-token.txt"
  contributions_dir = args.contributions_dir
  if contributions_dir is None:
    contributions_dir, error = _discover_contributions_dir(
      data_dir, args.api_base_url, token_file,
    )
    if contributions_dir is None:
      _unavailable_payload(args.output, error or "ledger-unavailable", args.apply)
      return 0

  lock_path = data_dir / "cron-logs" / "reflection-housekeeping.lock"
  lock_path.parent.mkdir(parents=True, exist_ok=True)
  with lock_path.open("a+", encoding="utf-8") as lock:
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
      _unavailable_payload(args.output, "housekeeping-already-running", args.apply)
      return 0
    run_housekeeping(
      data_dir=data_dir,
      contributions_dir=contributions_dir,
      output=args.output,
      apply=args.apply,
      upstream_ref=args.upstream_ref,
    )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
