#!/usr/bin/env python3
"""Stage Memory's bounded personalization profile for Reflection."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def _get(url: str, token: str):
  request = urllib.request.Request(url, headers={
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
  })
  with urllib.request.urlopen(request, timeout=20) as response:
    return json.load(response)


def bounded_profile(value: object) -> dict:
  source = (
    value
    if isinstance(value, dict) and value.get("schema") == 1
    else {}
  )
  confirmed = source.get("confirmed") if isinstance(source.get("confirmed"), list) else []

  def texts(name: str) -> list[str]:
    raw = source.get(name) if isinstance(source.get(name), list) else []
    return [
      " ".join(str(item).split())[:300]
      for item in raw[:24]
      if str(item).strip()
    ]

  safe_confirmed = []
  for item in confirmed[:48]:
    if isinstance(item, dict):
      safe_confirmed.append({
        key: str(item.get(key) or "")[:limit]
        for key, limit in (
          ("id", 160), ("title", 200), ("description", 500),
          ("path", 240), ("updated", 80),
        )
      })
  return {
    "schema": 1,
    "available": bool(source),
    "staged_at": datetime.now(UTC).isoformat(),
    "generated_at": str(source.get("generated_at") or "")[:80],
    "source_commit": str(source.get("source_commit") or "")[:80],
    "confirmed": safe_confirmed,
    "priorities": texts("priorities"),
    "boundaries": texts("boundaries"),
    "hypotheses": texts("hypotheses"),
  }


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--api-base-url", required=True)
  parser.add_argument("--token-file", required=True)
  parser.add_argument("--output", required=True)
  args = parser.parse_args()
  token = Path(args.token_file).read_text().strip()
  apps = _get(f"{args.api_base_url.rstrip('/')}/api/apps/", token)
  rows = apps if isinstance(apps, list) else apps.get("apps", [])
  memory = next(
    (row for row in rows if isinstance(row, dict) and row.get("slug") == "memory"),
    None,
  )
  value = {}
  if memory and str(memory.get("id", "")).isdigit():
    try:
      value = _get(
        f"{args.api_base_url.rstrip('/')}/api/storage/apps/"
        f"{memory['id']}/personalization-profile.json",
        token,
      )
    except (
      OSError, ValueError, TimeoutError, urllib.error.HTTPError,
      urllib.error.URLError,
    ):
      value = {}
  output = Path(args.output)
  output.parent.mkdir(parents=True, exist_ok=True)
  temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
  temp.write_text(
    json.dumps(bounded_profile(value), separators=(",", ":")) + "\n",
  )
  os.replace(temp, output)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
