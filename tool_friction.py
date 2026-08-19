#!/usr/bin/env python3
"""Summarise recurring agent-tool friction not covered by the prior run.

This is read-only evidence for Reflection and manager sessions. It deliberately
reports a few broad mechanical surfaces rather than diagnosing every command
failure. The agent still decides what matters; this helper makes repeated
plumbing visible in one bounded read.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shlex
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


VERSION = 5
DEFAULT_DB = "/data/db/ultimate.db"

_CODEX_SKILL_ENTRY = re.compile(
  r"(?<![A-Za-z0-9._/-])(?:/[^\s'\"`;&|()<>]+)?/\.codex/skills/"
  r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)/SKILL\.md\b",
)
_SHARED_SKILL_ENTRY = re.compile(
  r"(?<![A-Za-z0-9._/-])(?:/[^\s'\"`;&|()<>]+)?/shared/skills/"
  r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
  r"(?:\.md\b|/(?i:SKILL\.md)\b)",
)

PRIMITIVES = {
  "visual_capture": re.compile(
    r"agent-screenshot\.sh|preview_(?:app|shell)\.sh|\bagent-browser\b", re.I,
  ),
  "source_inspection": re.compile(r"\b(?:rg|grep\s+-R)\b", re.I),
  "tests": re.compile(
    r"\bpytest\b|\bnpm\s+test\b|scripts/test\.sh|wt-pytest\.sh", re.I,
  ),
  "git_write": re.compile(
    r"\bgit(?:\s+-C\s+\S+)?\s+(?:commit|cherry-pick|rebase|merge)\b|\bpm-commit\b",
    re.I,
  ),
  "contribution_work": re.compile(
    r"\bgh\s+(?:pr|run|api)\b|/contributions(?:/|\b)|\bContribute\b", re.I,
  ),
}


def _utc_now() -> dt.datetime:
  return dt.datetime.now(dt.timezone.utc)


def _message_time_ms(message: dict[str, Any]) -> int:
  try:
    return int(message.get("ts") or 0)
  except (TypeError, ValueError):
    return 0


def _tool_text(value: Any) -> str:
  if isinstance(value, str):
    return value
  try:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
  except (TypeError, ValueError):
    return str(value)


def _command_text(text: str) -> str:
  """Extract a shell command from serialized tool input once for all metrics."""
  try:
    value = json.loads(text)
  except json.JSONDecodeError:
    return text
  if isinstance(value, dict):
    command = value.get("cmd") or value.get("command")
    return str(command) if command is not None else ""
  return text


def _failed(block: dict[str, Any]) -> bool:
  return (
    block.get("status") in {"error", "failed"}
    or block.get("output_exit_code") not in (None, 0)
  )


def _failure_class(block: dict[str, Any]) -> str | None:
  """Classify mechanics without retaining potentially private tool output."""
  if block.get("status") in {"error", "failed"}:
    return "tool_reported_failure"
  if block.get("output_exit_code") not in (None, 0):
    return "nonzero_exit"
  return None


def _ratio(part: int | float, whole: int | float) -> float:
  return round(float(part) / float(whole), 4) if whole else 0.0


def _command_signature(tool: str, text: str) -> str:
  compact = " ".join(text.split())
  return hashlib.sha256(f"{tool}\0{compact}".encode("utf-8")).hexdigest()[:16]


def _command_family(tool: str, text: str) -> str:
  """Return a useful command family without retaining arguments or paths."""
  command = _command_text(text)
  compact = " ".join(command.split())
  patterns = (
    (r"\bwt-pytest\.sh\b", "backend focused tests"),
    (r"\bscripts/test\.sh\b|(?:^|\s)test\.sh\b", "platform test wrapper"),
    (r"\bpytest\b", "pytest"),
    (r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|vitest)\b", "frontend tests"),
    (r"\bagent-screenshot\.sh\b", "authenticated screenshot"),
    (r"\bagent-browser\b", "browser interaction"),
    (r"\b(?:rg|grep)\b", "source search"),
    (r"\bpm-commit\b", "scoped commit"),
    (r"\bgh\s+(pr|run|api)\b", None),
    (r"\bgit(?:\s+-C\s+\S+)?\s+(commit|cherry-pick|rebase|merge|status|diff|log)\b", None),
  )
  for pattern, label in patterns:
    match = re.search(pattern, compact, re.I)
    if not match:
      continue
    if label:
      return label
    verb = match.group(1).lower()
    owner = "GitHub" if pattern.startswith("\\bgh") else "git"
    return f"{owner} {verb}"
  try:
    words = shlex.split(compact)
  except ValueError:
    words = compact.split()
  if words:
    executable = Path(words[0]).name
    if executable in {"bash", "sh", "zsh"}:
      return "shell command"
    if executable in {"cat", "curl", "find", "python", "python3", "sed"}:
      return executable
  return tool


def _skill_entry_names(command: str) -> tuple[set[str], set[str]]:
  """Project-local and authoritative skill entries named by one command."""
  return (
    {match.group("name") for match in _CODEX_SKILL_ENTRY.finditer(command)},
    {match.group("name") for match in _SHARED_SKILL_ENTRY.finditer(command)},
  )


def _empty_surface() -> dict[str, Any]:
  return {
    "tool_calls": 0,
    "failed_calls": 0,
    "truncated_calls": 0,
    "output_bytes": 0,
    "chat_ids": set(),
  }


def _serialise_surface(value: dict[str, Any]) -> dict[str, Any]:
  result = {
    "tool_calls": value["tool_calls"],
    "failed_calls": value["failed_calls"],
    "truncated_calls": value["truncated_calls"],
    "output_bytes": value["output_bytes"],
    "chat_count": len(value["chat_ids"]),
  }
  result["failure_rate"] = _ratio(result["failed_calls"], result["tool_calls"])
  result["truncation_rate"] = _ratio(
    result["truncated_calls"], result["tool_calls"],
  )
  return result


def analyse_database(
  db_path: str = DEFAULT_DB,
  *,
  since: str | None = None,
  hours: int | None = None,
  now: dt.datetime | None = None,
  chat_limit: int = 12,
  repeated_limit: int = 12,
) -> dict[str, Any]:
  now = now or _utc_now()
  if now.tzinfo is None:
    now = now.replace(tzinfo=dt.timezone.utc)
  if since is None:
    cutoff = now - dt.timedelta(hours=max(1, hours or 24))
  else:
    cutoff = dt.datetime.fromisoformat(since.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
      cutoff = cutoff.replace(tzinfo=dt.timezone.utc)
    cutoff = cutoff.astimezone(dt.timezone.utc)
  cutoff_ms = int(cutoff.timestamp() * 1000)
  con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
  con.row_factory = sqlite3.Row

  run_rows: list[sqlite3.Row] = []
  chat_ids: list[str] = []
  try:
    run_rows = list(con.execute(
      "select chat_id, status, cost_usd, started_at, input_tokens, output_tokens, "
      "cache_read_input_tokens, total_tokens from chat_runs "
      "where started_at >= ?",
      (cutoff.replace(tzinfo=None).isoformat(sep=" "),),
    ))
    chat_ids = list(dict.fromkeys(
      str(row["chat_id"]) for row in run_rows if row["chat_id"]
    ))
  except sqlite3.Error:
    run_rows = []

  if chat_ids:
    chat_rows = []
    for offset in range(0, len(chat_ids), 400):
      chunk = chat_ids[offset : offset + 400]
      marks = ",".join("?" for _ in chunk)
      chat_rows.extend(con.execute(
        f"select id, title, messages from chats where id in ({marks})", chunk,
      ))
  else:
    chat_rows = list(con.execute("select id, title, messages from chats"))

  overall = _empty_surface()
  tool_types: Counter[str] = Counter()
  surfaces = defaultdict(_empty_surface)
  repeated: dict[tuple[str, str], dict[str, Any]] = {}
  command_families: dict[str, dict[str, Any]] = {}
  by_chat: dict[str, dict[str, Any]] = {}
  assistant_turns = 0
  failure_classes: Counter[str] = Counter()
  failure_families: Counter[str] = Counter()
  skill_indirections = {
    "chains": 0,
    "extra_tool_calls": 0,
    "candidate_output_bytes": 0,
    "chat_ids": set(),
    "skills": Counter(),
  }
  exact_repeat_candidates: dict[str, dict[str, Any]] = {}
  daily: dict[str, dict[str, Any]] = defaultdict(lambda: {
    "tool_calls": 0, "failed_calls": 0, "truncated_calls": 0,
    "completed_runs": 0, "cost_usd": 0.0, "total_tokens": 0,
    "input_tokens": 0, "cache_read_input_tokens": 0,
  })

  for row in chat_rows:
    chat_id = str(row["id"])
    title = str(row["title"] or "Untitled chat")
    try:
      messages = json.loads(row["messages"] or "[]")
    except (TypeError, ValueError):
      continue
    if not isinstance(messages, list):
      continue
    chat = {
      "chat_id": chat_id,
      "title": title,
      "assistant_turns": 0,
      "tool_calls": 0,
      "failed_calls": 0,
      "truncated_calls": 0,
      "output_bytes": 0,
    }
    for message in messages:
      if (
        not isinstance(message, dict)
        or message.get("role") != "assistant"
        or _message_time_ms(message) < cutoff_ms
      ):
        continue
      assistant_turns += 1
      chat["assistant_turns"] += 1
      day = dt.datetime.fromtimestamp(
        _message_time_ms(message) / 1000, tz=dt.timezone.utc,
      ).date().isoformat()
      message_tools: list[dict[str, Any]] = []
      for block in message.get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "tool":
          continue
        tool = str(block.get("tool") or "unknown")
        command = _tool_text(block.get("input"))
        searchable_command = _command_text(command)
        failed = _failed(block)
        failure_class = _failure_class(block)
        truncated = bool(block.get("output_truncated"))
        try:
          output_bytes = int(block.get("output_full_len") or 0)
        except (TypeError, ValueError):
          output_bytes = 0

        overall["tool_calls"] += 1
        overall["failed_calls"] += int(failed)
        overall["truncated_calls"] += int(truncated)
        overall["output_bytes"] += output_bytes
        overall["chat_ids"].add(chat_id)
        tool_types[tool] += 1
        chat["tool_calls"] += 1
        chat["failed_calls"] += int(failed)
        chat["truncated_calls"] += int(truncated)
        chat["output_bytes"] += output_bytes
        daily[day]["tool_calls"] += 1
        daily[day]["failed_calls"] += int(failed)
        daily[day]["truncated_calls"] += int(truncated)
        if failure_class:
          failure_classes[failure_class] += 1
          failure_families[_command_family(tool, command)] += 1

        for name, pattern in PRIMITIVES.items():
          if not pattern.search(searchable_command):
            continue
          surface = surfaces[name]
          surface["tool_calls"] += 1
          surface["failed_calls"] += int(failed)
          surface["truncated_calls"] += int(truncated)
          surface["output_bytes"] += output_bytes
          surface["chat_ids"].add(chat_id)

        signature = _command_signature(tool, command)
        family = _command_family(tool, command)
        family_item = command_families.setdefault(family, {
          "family": family, "count": 0, "chat_ids": set(), "failed_calls": 0,
          "truncated_calls": 0, "output_bytes": 0,
        })
        family_item["count"] += 1
        family_item["chat_ids"].add(chat_id)
        family_item["failed_calls"] += int(failed)
        family_item["truncated_calls"] += int(truncated)
        family_item["output_bytes"] += output_bytes
        key = (tool, signature)
        item = repeated.setdefault(key, {
          "tool": tool,
          "signature": signature,
          "family": family,
          "count": 0,
          "chat_ids": set(),
          "failed_calls": 0,
        })
        item["count"] += 1
        item["chat_ids"].add(chat_id)
        item["failed_calls"] += int(failed)
        project_skills, shared_skills = _skill_entry_names(searchable_command)
        message_tools.append({
          "tool": tool,
          "signature": signature,
          "family": family,
          "failed": failed,
          # Write/edit transcript blocks retain only their target path, not the
          # full mutation. Two edits to one file are therefore not proven
          # duplicates even when their privacy-safe signatures match.
          "repeatable": (
            bool(searchable_command.strip())
            and tool not in {"Edit", "Write", "MultiEdit", "NotebookEdit"}
          ),
          "output_bytes": output_bytes,
          "project_skills": project_skills,
          "shared_skills": shared_skills,
        })

      # A pointer-style project skill followed later in the same physical turn
      # by its authoritative shared entry is one causal chain. This is the
      # relationship broad command families and privacy-safe hashes cannot
      # reveal on their own. Keep only counts + skill ids; never command text.
      project_occurrences: dict[str, list[int]] = defaultdict(list)
      shared_occurrences: dict[str, list[int]] = defaultdict(list)
      for index, item in enumerate(message_tools):
        for name in item["project_skills"]:
          project_occurrences[name].append(index)
        for name in item["shared_skills"]:
          shared_occurrences[name].append(index)
      redirected_project_calls: set[int] = set()
      for name in project_occurrences.keys() & shared_occurrences.keys():
        pairs = [
          (project_index, shared_index)
          for project_index in project_occurrences[name]
          for shared_index in shared_occurrences[name]
          if project_index < shared_index
        ]
        if not pairs:
          continue
        skill_indirections["chains"] += 1
        skill_indirections["skills"][name] += 1
        skill_indirections["chat_ids"].add(chat_id)
        redirected_project_calls.update(project_index for project_index, _ in pairs)
      skill_indirections["extra_tool_calls"] += len(redirected_project_calls)
      skill_indirections["candidate_output_bytes"] += sum(
        message_tools[index]["output_bytes"] for index in redirected_project_calls
      )

      # Repeating the exact same successful call inside one physical turn can
      # be legitimate after an intervening edit, so expose it as a bounded
      # candidate—not a diagnosis. Failed-call retries are deliberately absent.
      seen_successful: set[str] = set()
      for item in message_tools:
        if item["failed"] or not item["repeatable"]:
          continue
        signature = item["signature"]
        if signature not in seen_successful:
          seen_successful.add(signature)
          continue
        family = item["family"]
        repeat = exact_repeat_candidates.setdefault(family, {
          "family": family,
          "extra_tool_calls": 0,
          "candidate_output_bytes": 0,
          "chat_ids": set(),
        })
        repeat["extra_tool_calls"] += 1
        repeat["candidate_output_bytes"] += item["output_bytes"]
        repeat["chat_ids"].add(chat_id)
    if chat["assistant_turns"]:
      by_chat[chat_id] = chat

  completed_runs = [row for row in run_rows if row["status"] == "completed"]
  for row in completed_runs:
    try:
      started = dt.datetime.fromisoformat(str(row["started_at"]))
      if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
      day = started.astimezone(dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError):
      continue
    daily[day]["completed_runs"] += 1
    daily[day]["cost_usd"] += float(row["cost_usd"] or 0)
    daily[day]["total_tokens"] += int(row["total_tokens"] or 0)
    daily[day]["input_tokens"] += int(row["input_tokens"] or 0)
    daily[day]["cache_read_input_tokens"] += int(
      row["cache_read_input_tokens"] or 0
    )
  run_totals = {
    "completed_runs": len(completed_runs),
    "chat_count": len({str(row["chat_id"]) for row in completed_runs}),
    "cost_usd": round(sum(float(row["cost_usd"] or 0) for row in completed_runs), 6),
    "input_tokens": sum(int(row["input_tokens"] or 0) for row in completed_runs),
    "output_tokens": sum(int(row["output_tokens"] or 0) for row in completed_runs),
    "cache_read_input_tokens": sum(
      int(row["cache_read_input_tokens"] or 0) for row in completed_runs
    ),
    "total_tokens": sum(int(row["total_tokens"] or 0) for row in completed_runs),
  }
  run_totals["cache_read_share"] = _ratio(
    run_totals["cache_read_input_tokens"], run_totals["input_tokens"],
  )
  run_totals["cost_per_completed_run"] = round(
    run_totals["cost_usd"] / run_totals["completed_runs"], 6,
  ) if run_totals["completed_runs"] else 0.0

  top_chats = sorted(
    by_chat.values(),
    key=lambda item: (
      item["failed_calls"], item["truncated_calls"], item["tool_calls"]
    ),
    reverse=True,
  )[:max(1, chat_limit)]
  repeated_rows = []
  for item in sorted(
    repeated.values(),
    key=lambda value: (len(value["chat_ids"]), value["count"]),
    reverse=True,
  ):
    if item["count"] < 2:
      continue
    repeated_rows.append({
      "tool": item["tool"],
      "signature": item["signature"],
      "family": item["family"],
      "count": item["count"],
      "chat_count": len(item["chat_ids"]),
      "failed_calls": item["failed_calls"],
    })
    if len(repeated_rows) >= max(1, repeated_limit):
      break

  con.close()
  serial_overall = _serialise_surface(overall)
  serial_overall["assistant_turns"] = assistant_turns
  return {
    "version": VERSION,
    "generated_at": now.isoformat(),
    "window": {"since": cutoff.isoformat(), "through": now.isoformat()},
    "overall": serial_overall,
    "run_totals": run_totals,
    "tool_types": dict(tool_types.most_common()),
    "failure_classes": dict(failure_classes.most_common()),
    "failure_families": dict(failure_families.most_common(12)),
    "daily": [
      {
        "date": day,
        **{
          **values,
          "cost_usd": round(values["cost_usd"], 6),
          "failure_rate": _ratio(values["failed_calls"], values["tool_calls"]),
          "truncation_rate": _ratio(
            values["truncated_calls"], values["tool_calls"],
          ),
          "cache_read_share": _ratio(
            values["cache_read_input_tokens"], values["input_tokens"],
          ),
        },
      }
      for day, values in sorted(daily.items())
    ],
    "primitives": {
      name: _serialise_surface(surfaces[name]) for name in PRIMITIVES
    },
    "avoidable_call_candidates": {
      "skill_read_indirection": {
        "chains": skill_indirections["chains"],
        "extra_tool_calls": skill_indirections["extra_tool_calls"],
        "candidate_output_bytes": skill_indirections["candidate_output_bytes"],
        "chat_count": len(skill_indirections["chat_ids"]),
        "top_skills": [
          {"skill": name, "chains": count}
          for name, count in skill_indirections["skills"].most_common(8)
        ],
      },
      "same_turn_exact_success_repeats": [
        {
          "family": item["family"],
          "extra_tool_calls": item["extra_tool_calls"],
          "candidate_output_bytes": item["candidate_output_bytes"],
          "chat_count": len(item["chat_ids"]),
        }
        for item in sorted(
          exact_repeat_candidates.values(),
          key=lambda value: (
            value["extra_tool_calls"], value["candidate_output_bytes"],
          ),
          reverse=True,
        )[:max(1, repeated_limit)]
      ],
    },
    "top_chats": top_chats,
    "repeated_calls": repeated_rows,
    "command_families": [
      {
        "family": item["family"],
        "count": item["count"],
        "chat_count": len(item["chat_ids"]),
        "failed_calls": item["failed_calls"],
        "truncated_calls": item["truncated_calls"],
        "output_bytes": item["output_bytes"],
      }
      for item in sorted(
        command_families.values(),
        key=lambda value: (len(value["chat_ids"]), value["count"]),
        reverse=True,
      )[:max(1, repeated_limit)]
    ],
    "truncating_command_families": [
      {
        "family": item["family"],
        "count": item["count"],
        "chat_count": len(item["chat_ids"]),
        "truncated_calls": item["truncated_calls"],
        "truncation_rate": _ratio(item["truncated_calls"], item["count"]),
        "output_bytes": item["output_bytes"],
      }
      for item in sorted(
        command_families.values(),
        key=lambda value: (value["truncated_calls"], value["output_bytes"]),
        reverse=True,
      )
      if item["truncated_calls"]
    ][:max(1, repeated_limit)],
  }


def format_report(data: dict[str, Any]) -> str:
  overall = data["overall"]
  runs = data["run_totals"]
  lines = [
    f"TOOL FRICTION — since {data['window']['since']}",
    "-" * 34,
    (
      f"  chats={overall['chat_count']}  assistant_turns={overall['assistant_turns']}  "
      f"tool_calls={overall['tool_calls']}  failed={overall['failed_calls']}  "
      f"truncated={overall['truncated_calls']}  "
      f"output={overall['output_bytes'] / 1_000_000:.1f}MB"
    ),
    (
      f"  completed_runs={runs['completed_runs']}  "
      f"recorded_cost=${runs['cost_usd']:.2f}  total_tokens={runs['total_tokens']}"
    ),
    (
      f"  failure_rate={overall['failure_rate']:.1%}  "
      f"truncation_rate={overall['truncation_rate']:.1%}  "
      f"cache_read_share={runs['cache_read_share']:.1%}  "
      f"cost/run=${runs['cost_per_completed_run']:.2f}"
    ),
    "  recurring mechanical surfaces:",
  ]
  for name, item in data["primitives"].items():
    lines.append(
      f"    {name:20} calls={item['tool_calls']:4}  failed={item['failed_calls']:3}  "
      f"truncated={item['truncated_calls']:3}  chats={item['chat_count']:3}"
    )
  candidates = data.get("avoidable_call_candidates") or {}
  indirection = candidates.get("skill_read_indirection") or {}
  if indirection.get("chains"):
    lines.append("  avoidable-call candidates:")
    lines.append(
      "    skill read indirection "
      f"chains={indirection['chains']}  "
      f"extra_calls={indirection['extra_tool_calls']}  "
      f"chats={indirection['chat_count']}"
    )
  for item in (candidates.get("same_turn_exact_success_repeats") or [])[:4]:
    lines.append(
      f"    repeated {item['family'][:22]:22} "
      f"extra_calls={item['extra_tool_calls']:3}  chats={item['chat_count']:3}"
    )
  if data["top_chats"]:
    lines.append("  highest-friction chats:")
    for item in data["top_chats"][:8]:
      title = " ".join(item["title"].split())
      if len(title) > 54:
        title = title[:53] + "…"
      lines.append(
        f"    {item['chat_id'][:8]}  calls={item['tool_calls']:4}  "
        f"failed={item['failed_calls']:3}  truncated={item['truncated_calls']:3}  {title}"
      )
  families = data.get("command_families") or []
  if families:
    lines.append("  most-used command families:")
    for item in families[:8]:
      lines.append(
        f"    {item['family'][:28]:28} calls={item['count']:3}  "
        f"failed={item['failed_calls']:3}  chats={item['chat_count']:3}"
      )
  truncating = data.get("truncating_command_families") or []
  if truncating:
    lines.append("  most-truncated command families:")
    for item in truncating[:6]:
      lines.append(
        f"    {item['family'][:28]:28} truncated={item['truncated_calls']:3}  "
        f"rate={item['truncation_rate']:.1%}  "
        f"output={item['output_bytes'] / 1_000_000:.1f}MB"
      )
  failures = data.get("failure_families") or {}
  if failures:
    lines.append("  most common failed command families:")
    for family, count in list(failures.items())[:8]:
      lines.append(f"    {family[:36]:36} failures={count:3}")
  return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--db", default=DEFAULT_DB)
  window = parser.add_mutually_exclusive_group()
  window.add_argument("--since")
  window.add_argument("--hours", type=int)
  parser.add_argument("--output")
  parser.add_argument("--json", action="store_true")
  args = parser.parse_args(list(argv) if argv is not None else None)
  result = analyse_database(args.db, since=args.since, hours=args.hours)
  if args.output:
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
  if args.json:
    print(json.dumps(result, indent=2, sort_keys=True))
  elif not args.output:
    print(format_report(result))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
