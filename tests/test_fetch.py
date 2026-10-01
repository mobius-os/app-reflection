"""The daily job opens a Reflection chat only when friction is pending."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

FETCH = Path(__file__).resolve().parents[1] / "fetch.sh"


@pytest.fixture
def api():
  requests = []

  class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
      body = self.rfile.read(int(self.headers["Content-Length"]))
      requests.append((self.path, self.headers["Authorization"], json.loads(body)))
      self.send_response(200)
      self.end_headers()
      self.wfile.write(b"{}")

    def log_message(self, *_args):
      pass

  server = HTTPServer(("127.0.0.1", 0), Handler)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  yield f"http://127.0.0.1:{server.server_port}", requests
  server.shutdown()


def _run(tmp_path, base_url):
  return subprocess.run(
    ["bash", str(FETCH), "7"],
    env={**os.environ, "DATA_DIR": str(tmp_path), "API_BASE_URL": base_url,
         "APP_TOKEN": "app-token"},
    capture_output=True, text=True, check=False,
  )


def test_no_pending_friction_starts_nothing(tmp_path, api):
  base_url, requests = api
  (tmp_path / "apps" / "7").mkdir(parents=True)

  assert _run(tmp_path, base_url).returncode == 0
  assert requests == []


def test_pending_friction_opens_one_chat_per_run(tmp_path, api):
  base_url, requests = api
  storage = tmp_path / "apps" / "7"
  storage.mkdir(parents=True)
  (storage / "friction.jsonl").write_text(
    json.dumps({"id": "a", "friction": "x"}) + "\n"
    + json.dumps({"id": "b", "friction": "y"}) + "\n"
  )
  (storage / "outcomes.jsonl").write_text(
    json.dumps({"friction_id": "a", "outcome": "explained"}) + "\n"
  )

  result = _run(tmp_path, base_url)

  assert result.returncode == 0, result.stderr
  ((path, auth, body),) = requests
  assert path == "/api/app-chats/start"
  assert auth == "Bearer app-token"
  assert body["scope"].startswith("run-")
  assert body["title"].startswith("Reflection — ")
  assert "owner_visible" not in body  # runs live in the app, not the chat list
  assert "provider" not in body and "model" not in body
  assert body["content"].startswith("Friction entries waiting for an outcome: 1.")
  assert "`reflection` skill" in body["content"]


def test_a_reportless_prior_outcome_is_retried_by_the_daily_job(tmp_path, api):
  base_url, requests = api
  storage = tmp_path / "apps" / "7"
  storage.mkdir(parents=True)
  (storage / "friction.jsonl").write_text(json.dumps({"id": "a", "friction": "x"}) + "\n")
  (storage / "outcomes.jsonl").write_text(json.dumps({
    "friction_id": "a", "outcome": "asked", "run": "interrupted-run",
  }) + "\n")

  assert _run(tmp_path, base_url).returncode == 0
  assert len(requests) == 1
  assert requests[0][2]["content"].startswith("Friction entries waiting for an outcome: 1.")


def test_a_later_run_the_same_day_is_not_swallowed_by_an_earlier_one(tmp_path, api):
  base_url, requests = api
  storage = tmp_path / "apps" / "7"
  storage.mkdir(parents=True)
  (storage / "friction.jsonl").write_text(json.dumps({"id": "a", "friction": "x"}) + "\n")

  _run(tmp_path, base_url)
  time.sleep(1.1)
  _run(tmp_path, base_url)

  first, second = (body["scope"] for _path, _auth, body in requests)
  assert first != second


def test_the_owners_chosen_agent_runs_the_chat(tmp_path, api):
  base_url, requests = api
  storage = tmp_path / "apps" / "7"
  storage.mkdir(parents=True)
  (storage / "friction.jsonl").write_text(json.dumps({"id": "a", "friction": "x"}) + "\n")
  (storage / "settings.json").write_text(json.dumps({"provider": "codex", "model": "gpt-5.6-sol"}))

  _run(tmp_path, base_url)

  ((_path, _auth, body),) = requests
  assert (body["provider"], body["model"]) == ("codex", "gpt-5.6-sol")
