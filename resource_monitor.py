#!/usr/bin/env python3
"""Bounded resource telemetry and decision logging for Reflection.

The nightly wrapper runs ``snapshot`` once.  A cheap pulse (filesystem and
cgroup counters) always runs; the recursive /data scan runs only when due or
when a pressure/growth trigger fires.  Reflection uses ``record`` after a
resource decision so later nights can reuse the evidence instead of repeating
the same diagnostic commands.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


VERSION = 3
DEFAULT_DEEP_INTERVAL_DAYS = 7
DEFAULT_DEEP_BUDGET_SECONDS = 30
DEFAULT_HISTORY_ENTRIES = 90
DEFAULT_LEDGER_ENTRIES = 200
DEFAULT_MAX_LOG_BYTES = 2 * 1024 * 1024
DEFAULT_WARN_PERCENT = 75
DEFAULT_CRITICAL_PERCENT = 85
DEFAULT_MEMORY_TIMEOUT_SECONDS = 3
SESSION_AGE_DAYS = (14, 45)
CACHE_PARTS = {
  "Cache", "Code Cache", "GPUCache", "DawnGraphiteCache",
  "DawnWebGPUCache", "GrShaderCache", "GraphiteDawnCache", "ShaderCache",
}


def _now() -> dt.datetime:
  return dt.datetime.now(dt.timezone.utc)


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
  try:
    value = int(os.environ.get(name, str(default)))
  except ValueError:
    return default
  return value if value >= minimum else default


def _read_json(path: Path) -> dict[str, Any]:
  try:
    value = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return {}
  return value if isinstance(value, dict) else {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
  with tmp.open("w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    fh.write("\n")
    fh.flush()
    os.fsync(fh.fileno())
  os.replace(tmp, path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
  try:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError:
    return []
  rows = []
  for line in lines:
    try:
      value = json.loads(line)
    except json.JSONDecodeError:
      continue
    if isinstance(value, dict):
      rows.append(value)
  return rows


def _append_bounded(
  path: Path,
  payload: dict[str, Any],
  *,
  max_entries: int,
  max_bytes: int = DEFAULT_MAX_LOG_BYTES,
) -> None:
  rows = _read_jsonl(path)
  rows.append(payload)
  rows = rows[-max_entries:]
  encoded = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows]
  while len(encoded) > 1 and sum(len(line) + 1 for line in encoded) > max_bytes:
    encoded.pop(0)
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
  with tmp.open("w", encoding="utf-8") as fh:
    for line in encoded:
      fh.write(line + "\n")
    fh.flush()
    os.fsync(fh.fileno())
  os.replace(tmp, path)


def _read_int(path: Path) -> int | str | None:
  try:
    raw = path.read_text(encoding="utf-8").strip()
  except OSError:
    return None
  if raw == "max":
    return raw
  try:
    return int(raw)
  except ValueError:
    return None


def _key_value_file(path: Path) -> dict[str, int]:
  values: dict[str, int] = {}
  try:
    lines = path.read_text(encoding="utf-8").splitlines()
  except OSError:
    return values
  for line in lines:
    parts = line.split()
    if len(parts) == 2:
      try:
        values[parts[0]] = int(parts[1])
      except ValueError:
        pass
  return values


def _io_totals(path: Path) -> dict[str, int]:
  totals: dict[str, int] = {}
  try:
    lines = path.read_text(encoding="utf-8").splitlines()
  except OSError:
    return totals
  for line in lines:
    for field in line.split()[1:]:
      key, sep, value = field.partition("=")
      if not sep:
        continue
      try:
        totals[key] = totals.get(key, 0) + int(value)
      except ValueError:
        pass
  return totals


def _runtime_counters() -> dict[str, Any]:
  root = Path(os.environ.get("REFLECTION_CGROUP_ROOT", "/sys/fs/cgroup"))
  result: dict[str, Any] = {}
  memory_current = _read_int(root / "memory.current")
  memory_max = _read_int(root / "memory.max")
  pids_current = _read_int(root / "pids.current")
  pids_max = _read_int(root / "pids.max")
  if memory_current is not None:
    result["memory_current_bytes"] = memory_current
  if memory_max is not None:
    result["memory_limit_bytes"] = memory_max
  if pids_current is not None:
    result["pids_current"] = pids_current
  if pids_max is not None:
    result["pids_limit"] = pids_max
  cpu = _key_value_file(root / "cpu.stat")
  if cpu:
    result["cpu"] = cpu
  io = _io_totals(root / "io.stat")
  if io:
    result["io"] = io
  return result


def _compact_memory_report(payload: object) -> dict[str, Any]:
  """Keep the durable owner trend small and free of process command lines."""
  if not isinstance(payload, dict):
    return {"available": False, "reason": "invalid-response"}
  process = payload.get("process")
  cgroup = payload.get("cgroup")
  processes = payload.get("processes")
  if not isinstance(process, dict) or not process.get("available"):
    return {"available": False, "reason": "server-memory-unavailable"}
  if not isinstance(cgroup, dict) or not cgroup.get("available"):
    return {"available": False, "reason": "cgroup-memory-unavailable"}

  server = {
    key: process.get(key)
    for key in (
      "pss_bytes", "rss_bytes", "anonymous_bytes", "swap_bytes",
      "threads", "uptime_seconds",
    )
    if isinstance(process.get(key), (int, float))
  }
  container = {
    key: cgroup.get(key)
    for key in (
      "current_bytes", "working_set_bytes", "limit_bytes",
      "swap_current_bytes", "anon_bytes", "file_bytes",
      "inactive_file_bytes", "kernel_bytes", "pressure",
    )
    if cgroup.get(key) is not None
  }
  owners: dict[str, dict[str, int]] = {}
  raw_groups = processes.get("groups", []) if isinstance(processes, dict) else []
  for group in raw_groups:
    if not isinstance(group, dict):
      continue
    category = group.get("category")
    if not isinstance(category, str) or not category:
      continue
    owners[category] = {
      key: value
      for key in (
        "process_count", "pss_bytes", "anonymous_bytes", "swap_bytes",
      )
      if isinstance((value := group.get(key)), int)
    }
  return {
    "available": True,
    "server": server,
    "container": container,
    "owners": dict(sorted(owners.items())),
  }


def _fetch_memory_report(
  api_base_url: str,
  token_file: Path,
  *,
  timeout_seconds: int = DEFAULT_MEMORY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
  """Fetch one authenticated sample; telemetry failure never fails Reflection."""
  try:
    token = token_file.read_text(encoding="utf-8").strip()
  except OSError:
    return {"available": False, "reason": "token-unavailable"}
  if not token or not api_base_url.strip():
    return {"available": False, "reason": "not-configured"}
  request = urllib.request.Request(
    api_base_url.rstrip("/")
    + "/api/debug/memory?allocation_limit=0&process_limit=0",
    headers={
      "Authorization": f"Bearer {token}",
      "Accept": "application/json",
      "User-Agent": "mobius-reflection-resource-monitor",
    },
  )
  try:
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
      payload = json.load(response)
  except urllib.error.HTTPError as exc:
    return {"available": False, "reason": f"http-{exc.code}"}
  except (OSError, ValueError, json.JSONDecodeError):
    return {"available": False, "reason": "request-failed"}
  return _compact_memory_report(payload)


def _memory_trend(
  previous: dict[str, Any] | None,
  current: dict[str, Any],
) -> dict[str, Any]:
  if not current.get("available"):
    return {
      "comparable_to_previous": False,
      "reason": "current-unavailable",
    }
  if not isinstance(previous, dict) or not previous.get("available"):
    return {
      "comparable_to_previous": False,
      "reason": "previous-unavailable",
    }

  result: dict[str, Any] = {
    "comparable_to_previous": True,
    "reason": None,
  }
  current_server = current.get("server") or {}
  previous_server = previous.get("server") or {}
  current_pss = current_server.get("pss_bytes")
  previous_pss = previous_server.get("pss_bytes")
  if isinstance(current_pss, int) and isinstance(previous_pss, int):
    result["server_pss_bytes_delta"] = current_pss - previous_pss

  current_container = current.get("container") or {}
  previous_container = previous.get("container") or {}
  current_working_set = current_container.get("working_set_bytes")
  previous_working_set = previous_container.get("working_set_bytes")
  if isinstance(current_working_set, int) and isinstance(previous_working_set, int):
    result["working_set_bytes_delta"] = (
      current_working_set - previous_working_set
    )

  current_owners = current.get("owners") or {}
  previous_owners = previous.get("owners") or {}
  owner_deltas = {}
  for category in sorted(set(current_owners) | set(previous_owners)):
    current_owner = current_owners.get(category) or {}
    previous_owner = previous_owners.get(category) or {}
    current_owner_pss = current_owner.get("pss_bytes", 0)
    previous_owner_pss = previous_owner.get("pss_bytes", 0)
    if isinstance(current_owner_pss, int) and isinstance(previous_owner_pss, int):
      owner_deltas[category] = current_owner_pss - previous_owner_pss
  result["owner_pss_bytes_delta"] = owner_deltas
  return result


def _parse_time(value: object) -> dt.datetime | None:
  if not isinstance(value, str):
    return None
  try:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=dt.timezone.utc)
  return parsed.astimezone(dt.timezone.utc)


def _deep_scan_reason(
  *,
  now: dt.datetime,
  disk: dict[str, Any],
  state: dict[str, Any],
  previous: dict[str, Any] | None,
) -> tuple[bool, str]:
  override = os.environ.get("REFLECTION_RESOURCE_DEEP_SCAN", "auto").strip().lower()
  if override in {"1", "true", "yes", "force"}:
    return True, "forced"
  if override in {"0", "false", "no", "skip"}:
    return False, "disabled"

  warn_percent = _env_int("REFLECTION_RESOURCE_WARN_PERCENT", DEFAULT_WARN_PERCENT)
  if disk["used_percent"] >= warn_percent:
    return True, "disk-pressure"

  previous_used = None
  if previous:
    previous_disk = previous.get("disk") or {}
    if not _same_filesystem(previous_disk, disk):
      return True, "filesystem-identity-changed"
    previous_used = previous_disk.get("used_bytes")
  if isinstance(previous_used, int):
    growth = disk["used_bytes"] - previous_used
    growth_trigger = max(256 * 1024**2, int(disk["total_bytes"] * 0.05))
    if growth >= growth_trigger:
      return True, "disk-growth"

  last_deep = _parse_time(state.get("last_complete_deep_scan_at"))
  if last_deep is None:
    return True, "first-scan"
  interval_days = _env_int(
    "REFLECTION_RESOURCE_DEEP_SCAN_DAYS", DEFAULT_DEEP_INTERVAL_DAYS, minimum=1,
  )
  if now - last_deep >= dt.timedelta(days=interval_days):
    return True, "scheduled"
  return False, "not-due"


def _filesystem_snapshot(path: Path, *, scope: str) -> dict[str, Any]:
  usage = shutil.disk_usage(path)
  try:
    device_id = path.stat().st_dev
  except OSError:
    device_id = None
  return {
    "scope": scope,
    "path": str(path),
    "device_id": device_id,
    "total_bytes": usage.total,
    "used_bytes": usage.used,
    "free_bytes": usage.free,
    "used_percent": round(100 * usage.used / usage.total, 2) if usage.total else 0,
  }


def _same_filesystem(previous: dict[str, Any], current: dict[str, Any]) -> bool:
  """Only compare trends from the same named scope and mounted device."""
  return bool(
    previous.get("scope") == current.get("scope")
    and previous.get("device_id") is not None
    and previous.get("device_id") == current.get("device_id")
    and previous.get("total_bytes") == current.get("total_bytes")
  )


def _pressure(used_percent: float, *, warn: int, critical: int) -> str:
  if used_percent >= critical:
    return "critical"
  if used_percent >= warn:
    return "warning"
  return "normal"


def _trend(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
  previous_disk = previous or {}
  comparable = _same_filesystem(previous_disk, current)
  previous_used = previous_disk.get("used_bytes")
  return {
    "comparable_to_previous": comparable,
    "reason": None if comparable else "filesystem-identity-changed",
    "used_bytes_delta": current["used_bytes"] - previous_used
    if comparable and isinstance(previous_used, int) else None,
  }


def _allocated_bytes(stat_result: os.stat_result) -> int:
  blocks = getattr(stat_result, "st_blocks", None)
  return blocks * 512 if isinstance(blocks, int) else stat_result.st_size


def _deep_scan(data_dir: Path, *, now: dt.datetime) -> dict[str, Any]:
  budget = _env_int(
    "REFLECTION_RESOURCE_DEEP_SCAN_BUDGET_SECONDS",
    DEFAULT_DEEP_BUDGET_SECONDS,
    minimum=1,
  )
  deadline = time.monotonic() + budget
  categories: dict[str, int] = {}
  browser_profiles: dict[str, int] = {}
  browser_cache_bytes = 0
  session_age = {
    "codex": {"older_than_14d_bytes": 0, "older_than_45d_bytes": 0},
    "claude": {"older_than_14d_bytes": 0, "older_than_45d_bytes": 0},
  }
  files_seen = 0
  dirs_seen = 0
  complete = True
  started = time.monotonic()
  try:
    root_device = data_dir.stat().st_dev
  except OSError:
    root_device = None
  seen_inodes: set[tuple[int, int]] = set()

  for root, dirs, files in os.walk(data_dir, followlinks=False):
    if time.monotonic() >= deadline:
      complete = False
      dirs.clear()
      break
    dirs_seen += 1
    root_path = Path(root)
    try:
      rel_root = root_path.relative_to(data_dir)
    except ValueError:
      dirs.clear()
      continue
    if root_device is not None:
      kept = []
      for name in dirs:
        try:
          if (root_path / name).stat(follow_symlinks=False).st_dev == root_device:
            kept.append(name)
        except OSError:
          pass
      dirs[:] = kept

    for name in files:
      if time.monotonic() >= deadline:
        complete = False
        dirs.clear()
        break
      path = root_path / name
      try:
        st = path.stat(follow_symlinks=False)
      except OSError:
        continue
      inode = (st.st_dev, st.st_ino)
      if inode in seen_inodes:
        continue
      seen_inodes.add(inode)
      files_seen += 1
      allocated = _allocated_bytes(st)
      parts = rel_root.parts
      category = parts[0] if parts else "_root_files"
      categories[category] = categories.get(category, 0) + allocated

      if category == "agent-browser-profiles" and len(parts) >= 2:
        profile = parts[1]
        browser_profiles[profile] = browser_profiles.get(profile, 0) + allocated
        if any(part in CACHE_PARTS for part in parts[2:]):
          browser_cache_bytes += allocated

      age_days = (now.timestamp() - st.st_mtime) / 86400
      rel = "/".join(parts)
      provider = None
      if rel.startswith("cli-auth/codex/sessions/"):
        provider = "codex"
      elif rel.startswith("cli-auth/claude/projects/"):
        provider = "claude"
      if provider:
        if age_days > SESSION_AGE_DAYS[0]:
          session_age[provider]["older_than_14d_bytes"] += allocated
        if age_days > SESSION_AGE_DAYS[1]:
          session_age[provider]["older_than_45d_bytes"] += allocated

  top_profiles = [
    {"name": name, "bytes": size}
    for name, size in sorted(browser_profiles.items(), key=lambda row: row[1], reverse=True)[:10]
  ]
  return {
    "complete": complete,
    "elapsed_seconds": round(time.monotonic() - started, 3),
    "budget_seconds": budget,
    "files_seen": files_seen,
    "directories_seen": dirs_seen,
    "top_level_bytes": dict(sorted(categories.items(), key=lambda row: row[1], reverse=True)),
    "browser_profiles": {
      "count": len(browser_profiles),
      "bytes": sum(browser_profiles.values()),
      "known_cache_bytes": browser_cache_bytes,
      "largest": top_profiles,
    },
    "session_history": session_age,
  }


def make_snapshot(
  data_dir: Path,
  *,
  history_path: Path,
  state_path: Path,
  runtime_root: Path | None = None,
  memory_report: dict[str, Any] | None = None,
  now: dt.datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
  now = now or _now()
  runtime_root = runtime_root or data_dir
  disk = _filesystem_snapshot(data_dir, scope="data-volume")
  root_disk = _filesystem_snapshot(runtime_root, scope="container-root")
  history = _read_jsonl(history_path)
  previous = history[-1] if history else None
  state = _read_json(state_path)
  run_deep, reason = _deep_scan_reason(
    now=now, disk=disk, state=state, previous=previous,
  )
  warn = _env_int("REFLECTION_RESOURCE_WARN_PERCENT", DEFAULT_WARN_PERCENT)
  critical = _env_int("REFLECTION_RESOURCE_CRITICAL_PERCENT", DEFAULT_CRITICAL_PERCENT)
  data_pressure = _pressure(disk["used_percent"], warn=warn, critical=critical)
  root_pressure = _pressure(root_disk["used_percent"], warn=warn, critical=critical)
  pressure_rank = {"normal": 0, "warning": 1, "critical": 2}
  pressure = max((data_pressure, root_pressure), key=pressure_rank.get)
  previous_disk = (previous.get("disk") or {}) if previous else None
  previous_root = (
    ((previous.get("filesystems") or {}).get("container_root") or {})
    if previous else None
  )
  memory = memory_report or {
    "available": False,
    "reason": "not-collected",
  }
  previous_memory = (previous.get("memory") or {}) if previous else None
  snapshot: dict[str, Any] = {
    "version": VERSION,
    "captured_at": now.isoformat(),
    "pressure": pressure,
    "disk": disk,
    "filesystems": {
      "data_volume": {**disk, "pressure": data_pressure},
      "container_root": {**root_disk, "pressure": root_pressure},
    },
    "runtime": _runtime_counters(),
    "memory": memory,
    "trend": {
      **_trend(previous_disk, disk),
      "container_root": _trend(previous_root, root_disk),
      "memory": _memory_trend(previous_memory, memory),
    },
    "deep_scan": {"ran": run_deep, "reason": reason},
  }
  if run_deep:
    deep = _deep_scan(data_dir, now=now)
    snapshot["deep_scan"].update(deep)
    state["last_deep_scan_attempt_at"] = now.isoformat()
    if deep["complete"]:
      state["last_complete_deep_scan_at"] = now.isoformat()

  interval = _env_int(
    "REFLECTION_RESOURCE_DEEP_SCAN_DAYS", DEFAULT_DEEP_INTERVAL_DAYS, minimum=1,
  )
  last_complete = _parse_time(state.get("last_complete_deep_scan_at"))
  snapshot["policy"] = {
    "deep_scan_interval_days": interval,
    "next_scheduled_deep_scan_at": (
      (last_complete + dt.timedelta(days=interval)).isoformat()
      if last_complete else now.isoformat()
    ),
    "history_entries_retained": _env_int(
      "REFLECTION_RESOURCE_HISTORY_ENTRIES", DEFAULT_HISTORY_ENTRIES, minimum=2,
    ),
  }
  state["last_snapshot_at"] = now.isoformat()
  state["last_used_bytes"] = disk["used_bytes"]
  return snapshot, state


def snapshot_command(args: argparse.Namespace) -> int:
  data_dir = Path(args.data_dir).resolve()
  history_path = Path(args.history)
  state_path = Path(args.state)
  memory_report = _fetch_memory_report(
    args.memory_api,
    Path(args.token_file),
    timeout_seconds=args.memory_timeout,
  )
  snapshot, state = make_snapshot(
    data_dir,
    history_path=history_path,
    state_path=state_path,
    runtime_root=Path(args.runtime_root).resolve(),
    memory_report=memory_report,
  )
  _atomic_json(Path(args.output), snapshot)
  _append_bounded(
    history_path,
    snapshot,
    max_entries=_env_int(
      "REFLECTION_RESOURCE_HISTORY_ENTRIES", DEFAULT_HISTORY_ENTRIES, minimum=2,
    ),
  )
  _atomic_json(state_path, state)
  return 0


def record_command(args: argparse.Namespace) -> int:
  payload: dict[str, Any] = {
    "version": VERSION,
    "recorded_at": _now().isoformat(),
    "area": args.area,
    "evidence": args.evidence,
    "action": args.action,
    "result": args.result,
    "next_review_at": args.next_review_at,
    "review_trigger": args.review_trigger,
  }
  if args.bytes_reclaimed is not None:
    payload["bytes_reclaimed"] = args.bytes_reclaimed
  _append_bounded(
    Path(args.ledger), payload,
    max_entries=_env_int(
      "REFLECTION_RESOURCE_LEDGER_ENTRIES", DEFAULT_LEDGER_ENTRIES, minimum=10,
    ),
  )
  return 0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)
  snapshot = sub.add_parser("snapshot", help="write a cheap pulse and adaptive deep scan")
  snapshot.add_argument("--data-dir", required=True)
  snapshot.add_argument(
    "--runtime-root", default="/",
    help="container-root filesystem pulse; never conflated with the data volume",
  )
  snapshot.add_argument("--output", required=True)
  snapshot.add_argument("--history", required=True)
  snapshot.add_argument("--state", required=True)
  snapshot.add_argument(
    "--memory-api", default=os.environ.get("API_BASE_URL", ""),
    help="Möbius base URL used for one owner-aware memory sample",
  )
  snapshot.add_argument(
    "--token-file", default="/data/service-token.txt",
    help="owner-scoped service token used for the memory sample",
  )
  snapshot.add_argument(
    "--memory-timeout", type=int, default=DEFAULT_MEMORY_TIMEOUT_SECONDS,
    help="maximum seconds for the optional memory sample",
  )
  snapshot.set_defaults(func=snapshot_command)

  record = sub.add_parser("record", help="append one bounded resource decision")
  record.add_argument("--ledger", required=True)
  record.add_argument("--area", required=True)
  record.add_argument("--evidence", required=True)
  record.add_argument("--action", required=True)
  record.add_argument("--result", required=True)
  record.add_argument("--next-review-at", required=True)
  record.add_argument("--review-trigger", required=True)
  record.add_argument("--bytes-reclaimed", type=int)
  record.set_defaults(func=record_command)
  return parser


def main(argv: list[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  return args.func(args)


if __name__ == "__main__":
  sys.exit(main())
