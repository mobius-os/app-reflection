"""Reflection's log_friction agent tool, as served through the app service."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service  # noqa: E402

CALL = {"chat_id": "chat-1", "run_id": "run-1", "provider": "claude", "call_id": "toolu_1"}


def _request(arguments, call=CALL):
  return {
    "schema": 1, "method": "POST", "path": "/tools/log_friction",
    "query": {}, "headers": {}, "public": False, "actor": {"scope": "platform"},
    "body": {"arguments": arguments, "call": call},
  }


@pytest.fixture
def storage(tmp_path, monkeypatch):
  monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path))
  return tmp_path


def _entries(storage):
  return [json.loads(line) for line in (storage / "friction.jsonl").read_text().splitlines()]


def test_logged_friction_keeps_the_exact_call_moment(storage):
  response = service.dispatch(_request({"friction": " retried a flaky command "}))

  assert response["status"] == 200
  (entry,) = _entries(storage)
  assert entry["friction"] == "retried a flaky command"
  assert entry["call"] == CALL
  assert entry["id"] and entry["at"]


def test_parallel_calls_each_append_one_whole_line(storage):
  with ThreadPoolExecutor(8) as pool:
    list(pool.map(
      lambda n: service.dispatch(_request({"friction": f"friction {n} " + "x" * 3000})),
      range(40),
    ))
  assert len(_entries(storage)) == 40


def test_empty_friction_is_a_tool_error(storage):
  assert service.dispatch(_request({"friction": " "}))["status"] == 422
  assert not (storage / "friction.jsonl").exists()


def test_friction_needs_the_platforms_chat_identity(storage):
  assert service.dispatch(_request({"friction": "x"}, call={}))["status"] == 403


def test_the_manifest_declares_exactly_the_served_tool():
  manifest = json.loads(
    (Path(__file__).resolve().parents[1] / "mobius.json").read_text("utf-8"),
  )
  assert [tool["name"] for tool in manifest["tools"]] == ["log_friction"]
  assert manifest["service"] == {"entry": "service.py"}
  assert "service.py" in manifest["source_files"]
