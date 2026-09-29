"""A run's outcomes become settled only with its published report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import friction_queue  # noqa: E402


@pytest.fixture(autouse=True)
def no_ambient_run(monkeypatch):
  monkeypatch.delenv("CHAT_ID", raising=False)


def _log(storage, *ids):
  with (storage / "friction.jsonl").open("a") as handle:
    for friction_id in ids:
      handle.write(json.dumps({"id": friction_id, "friction": f"f {friction_id}"}) + "\n")


def _decision(friction_id, outcome="explained", note="Checked."):
  return {"friction_id": friction_id, "outcome": outcome, "note": note}


def _publish(storage, monkeypatch, run, *decisions):
  monkeypatch.setenv("CHAT_ID", run)
  draft = storage / f"{run}-draft.md"
  draft.write_text(f"# Report from {run}\n\nConclusions.\n")
  return friction_queue.publish(storage, draft, list(decisions))


def test_nothing_is_pending_before_any_friction(tmp_path):
  assert friction_queue.pending(tmp_path) == []


def test_pending_is_every_unsettled_entry_oldest_first(tmp_path, monkeypatch):
  _log(tmp_path, "a", "b", "c")
  _publish(tmp_path, monkeypatch, "run-1", _decision("b"))
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["a", "c"]


def test_an_interrupted_run_resumes_where_it_stopped(tmp_path, monkeypatch):
  _log(tmp_path, "a", "b")
  _publish(tmp_path, monkeypatch, "run-1", _decision("a", "joined", "Same cause as b."))
  _log(tmp_path, "c")
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["b", "c"]


def test_a_partially_written_line_is_left_for_the_next_read(tmp_path):
  _log(tmp_path, "a")
  with (tmp_path / "friction.jsonl").open("a") as handle:
    handle.write('{"id": "b", "fric')
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["a"]


@pytest.mark.parametrize("decisions, message", [
  ([_decision("a", "fixed")], "outcome must be"),
  ([_decision("a", note=" ")], "note"),
  ([_decision("missing")], "entry was settled"),
  ([_decision("a"), _decision("a")], "distinct"),
  ([], "nonempty"),
])
def test_publish_rejects_invalid_outcomes_without_a_report(tmp_path, monkeypatch, decisions, message):
  _log(tmp_path, "a")
  with pytest.raises(ValueError, match=message):
    _publish(tmp_path, monkeypatch, "run-1", *decisions)
  assert not (tmp_path / "reports" / "run-1.md").exists()
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["a"]


def test_a_run_cannot_publish_without_its_chat_id(tmp_path):
  _log(tmp_path, "a")
  draft = tmp_path / "draft.md"
  draft.write_text("# Report\n")
  with pytest.raises(ValueError, match="CHAT_ID"):
    friction_queue.publish(tmp_path, draft, [_decision("a")])
  assert not (tmp_path / "outcomes.jsonl").exists()


def test_a_complete_report_is_published_before_entries_leave_the_queue(tmp_path, monkeypatch):
  _log(tmp_path, "a", "b")
  report = _publish(tmp_path, monkeypatch, "run-1", _decision("a", "asked", "Worth a fix."))
  assert report.read_text() == "# Report from run-1\n\nConclusions.\n"
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["b"]
  [record] = friction_queue._read_jsonl(tmp_path / "outcomes.jsonl")
  assert (record["run"], record["friction_id"], record["outcome"]) == ("run-1", "a", "asked")


def test_an_old_outcome_without_its_report_returns_to_the_queue(tmp_path, monkeypatch):
  _log(tmp_path, "a")
  (tmp_path / "outcomes.jsonl").write_text(json.dumps({
    "friction_id": "a", "outcome": "asked", "run": "interrupted-run",
  }) + "\n")
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["a"]
  _publish(tmp_path, monkeypatch, "next-run", _decision("a", "asked", "Recovered."))
  assert friction_queue.pending(tmp_path) == []


def test_a_partial_old_outcome_cannot_swallow_the_next_handoff(tmp_path, monkeypatch):
  _log(tmp_path, "a")
  (tmp_path / "outcomes.jsonl").write_text('{"friction_id":"cut-off"')
  _publish(tmp_path, monkeypatch, "run-1", _decision("a"))
  assert friction_queue.pending(tmp_path) == []
  assert len(friction_queue._read_jsonl(tmp_path / "outcomes.jsonl")) == 1


def test_overlapping_runs_cannot_publish_competing_outcomes(tmp_path, monkeypatch):
  _log(tmp_path, "a")
  _publish(tmp_path, monkeypatch, "first-run", _decision("a", "explained", "No fix."))
  with pytest.raises(ValueError, match="another run"):
    _publish(tmp_path, monkeypatch, "second-run", _decision("a", "asked", "Fix it."))
  assert not (tmp_path / "reports" / "second-run.md").exists()
  assert [record["run"] for record in friction_queue._read_jsonl(tmp_path / "outcomes.jsonl")] == ["first-run"]


def test_a_retry_cannot_replace_the_report_behind_published_outcomes(tmp_path, monkeypatch):
  _log(tmp_path, "a", "b")
  report = _publish(tmp_path, monkeypatch, "same-run", _decision("a"))
  original = report.read_bytes()
  with pytest.raises(ValueError, match="report cannot be replaced"):
    _publish(tmp_path, monkeypatch, "same-run", _decision("b"))
  assert report.read_bytes() == original
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["b"]


def test_empty_report_does_not_publish_outcomes(tmp_path, monkeypatch):
  _log(tmp_path, "a")
  monkeypatch.setenv("CHAT_ID", "run-1")
  draft = tmp_path / "draft.md"
  draft.write_text(" \n")
  with pytest.raises(ValueError, match="empty"):
    friction_queue.publish(tmp_path, draft, [_decision("a")])
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["a"]


def test_cli_publishes_report_and_outcomes_together(tmp_path, monkeypatch, capsys):
  _log(tmp_path, "a")
  monkeypatch.setenv("CHAT_ID", "run-1")
  draft = tmp_path / "draft.md"
  draft.write_text("# Report\n")
  outcomes = tmp_path / "selected.json"
  outcomes.write_text(json.dumps([_decision("a")]))
  assert friction_queue.main(["q", "pending", str(tmp_path)]) == 0
  assert json.loads(capsys.readouterr().out)[0]["id"] == "a"
  assert friction_queue.main(["q", "publish", str(tmp_path), str(draft), str(outcomes)]) == 0
  assert friction_queue.pending(tmp_path) == []
