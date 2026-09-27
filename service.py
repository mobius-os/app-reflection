#!/usr/bin/env python3
"""Reflection's agent tool, served through the platform's JSON-v1 app service.

``log_friction`` appends one entry to ``friction.jsonl`` in the app's storage:
the agent's own description plus the platform-authenticated moment of the
call (chat, run, provider, and the provider's call id), which lets a later
review fork the agent right after it logged the friction.
"""

from __future__ import annotations

from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import sys
import uuid

MAX_FRICTION_CHARS = 4000


def friction_log() -> Path:
  return Path(os.environ["APP_STORAGE_DIR"]) / "friction.jsonl"


def log_friction(arguments: dict, call: dict) -> dict:
  friction = arguments.get("friction")
  if not isinstance(friction, str) or not friction.strip():
    return {"status": 422, "body": {"detail": "Describe the friction in one short text."}}
  entry = {
    "id": uuid.uuid4().hex,
    "at": datetime.now(UTC).isoformat(timespec="seconds"),
    "friction": friction.strip()[:MAX_FRICTION_CHARS],
    "call": call,
  }
  path = friction_log()
  path.parent.mkdir(parents=True, exist_ok=True)
  line = json.dumps(entry, ensure_ascii=False) + "\n"
  # Tool calls run in parallel; one locked append keeps every line whole.
  with path.open("a", encoding="utf-8") as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    handle.write(line)
  return {"status": 200, "body": "Logged. Carry on with the task."}


def dispatch(request: dict) -> dict:
  if request.get("method") != "POST" or request.get("path") != "/tools/log_friction":
    return {"status": 404, "body": {"detail": "Not found."}}
  body = request.get("body") if isinstance(request.get("body"), dict) else {}
  call = body.get("call") if isinstance(body.get("call"), dict) else {}
  if not isinstance(call.get("chat_id"), str) or not call["chat_id"]:
    return {"status": 403, "body": {"detail": "Friction is logged only from an agent chat."}}
  arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
  return log_friction(arguments, call)


# Möbius may run everything above once and fork each request from it,
# removing interpreter start-up and imports from every request. Module setup
# therefore reads only per-installation values and starts no threads.
MOBIUS_PRELOAD = True

if __name__ == "__main__":
  request = json.loads(sys.stdin.read())
  print(json.dumps(dispatch(request), ensure_ascii=False, separators=(",", ":")))
