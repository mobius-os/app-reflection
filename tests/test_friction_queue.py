"""The friction backlog resumes from whatever still lacks an outcome."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import friction_queue  # noqa: E402


def _log(storage, *ids):
  with (storage / "friction.jsonl").open("a") as handle:
    for friction_id in ids:
      handle.write(json.dumps({"id": friction_id, "friction": f"f {friction_id}"}) + "\n")


def test_nothing_is_pending_before_any_friction(tmp_path):
  assert friction_queue.pending(tmp_path) == []


def test_pending_is_every_unsettled_entry_oldest_first(tmp_path):
  _log(tmp_path, "a", "b", "c")
  friction_queue.settle(tmp_path, "b", "explained", "A one-off network blip.")

  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["a", "c"]


def test_an_interrupted_run_resumes_where_it_stopped(tmp_path):
  _log(tmp_path, "a", "b")
  friction_queue.settle(tmp_path, "a", "joined", "Same cause as b.")
  _log(tmp_path, "c")
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["b", "c"]


def test_a_partially_written_line_is_left_for_the_next_read(tmp_path):
  _log(tmp_path, "a")
  with (tmp_path / "friction.jsonl").open("a") as handle:
    handle.write('{"id": "b", "fric')
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["a"]


@pytest.mark.parametrize("friction_id, outcome, note", [
  ("a", "fixed", "Not an outcome."),
  ("a", "explained", " "),
  ("missing", "explained", "No such entry."),
])
def test_settle_rejects_what_the_backlog_cannot_record(tmp_path, friction_id, outcome, note):
  _log(tmp_path, "a")
  with pytest.raises(ValueError):
    friction_queue.settle(tmp_path, friction_id, outcome, note)
  assert not (tmp_path / "outcomes.jsonl").exists()


def test_an_entry_is_settled_once(tmp_path):
  _log(tmp_path, "a")
  friction_queue.settle(tmp_path, "a", "explained", "Once.")
  with pytest.raises(ValueError, match="no pending"):
    friction_queue.settle(tmp_path, "a", "explained", "Twice.")


def test_cli_prints_pending_and_reports_bad_settles(tmp_path, capsys):
  _log(tmp_path, "a")
  assert friction_queue.main(["q", "pending", str(tmp_path)]) == 0
  assert json.loads(capsys.readouterr().out)[0]["id"] == "a"
  assert friction_queue.main(["q", "settle", str(tmp_path), "a", "fixed", "x"]) == 1
  assert friction_queue.main(["q", "settle", str(tmp_path), "a", "explained", "ok"]) == 0


def test_an_outcome_records_the_run_that_settled_it(tmp_path, monkeypatch):
  _log(tmp_path, "a", "b")
  monkeypatch.setenv("CHAT_ID", "run-chat")
  assert friction_queue.settle(tmp_path, "a", "asked", "Worth a fix.")["run"] == "run-chat"
  monkeypatch.delenv("CHAT_ID")
  assert "run" not in friction_queue.settle(tmp_path, "b", "explained", "Fine.")
