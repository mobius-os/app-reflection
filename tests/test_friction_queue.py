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


def test_proposal_history_keeps_revision_and_verified_evidence_without_resettling_friction(tmp_path, monkeypatch):
  _log(tmp_path, "ask", "other")
  _publish(tmp_path, monkeypatch, "run-1", _decision("ask", "asked", "Propose a fix."))
  revised = friction_queue.record_proposal(
    tmp_path, "ask", "revised", "chat:decision-1", "Narrowed the proposed change.")
  verified = friction_queue.record_proposal(
    tmp_path, "ask", "verified", "test:live-1", "Observed the live path after activation.")
  trail = friction_queue.history(tmp_path, "ask")
  assert [(item.get("outcome"), item.get("stage")) for item in trail] == [
    ("asked", None), (None, "revised"), (None, "verified")]
  assert trail[1:] == [revised, verified]
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["other"]


def test_proposal_event_requires_report_backed_ask_and_explicit_evidence(tmp_path, monkeypatch):
  _log(tmp_path, "a")
  with pytest.raises(ValueError, match="report-backed asked"):
    friction_queue.record_proposal(tmp_path, "a", "approved", "chat:yes", "Owner approved.")
  _publish(tmp_path, monkeypatch, "run-1", _decision("a", "asked", "Proposed."))
  for stage, reference, note, message in [
    ("fixed", "chat:yes", "Approved.", "valid proposal stage"),
    ("approved", "", "Approved.", "source reference"),
    ("approved", "chat:yes", " ", "evidence note"),
  ]:
    with pytest.raises(ValueError, match=message):
      friction_queue.record_proposal(tmp_path, "a", stage, reference, note)
  assert len(friction_queue._read_jsonl(tmp_path / "outcomes.jsonl")) == 1


def test_reportless_ask_cannot_gain_a_proposal_event(tmp_path):
  (tmp_path / "outcomes.jsonl").write_text(json.dumps({
    "friction_id": "a", "outcome": "asked", "run": "missing-report",
  }) + "\n")
  assert friction_queue.history(tmp_path, "a") == []
  with pytest.raises(ValueError, match="report-backed asked"):
    friction_queue.record_proposal(tmp_path, "a", "approved", "chat:yes", "Owner approved.")


def test_runless_legacy_ask_is_not_a_report_backed_proposal(tmp_path):
  (tmp_path / "outcomes.jsonl").write_text(json.dumps({
    "friction_id": "a", "outcome": "asked",
  }) + "\n")
  assert friction_queue.history(tmp_path, "a") == []
  with pytest.raises(ValueError, match="report-backed asked"):
    friction_queue.record_proposal(tmp_path, "a", "approved", "chat:yes", "Owner approved.")


def test_joined_outcome_links_to_an_existing_proposal_without_implying_approval(tmp_path, monkeypatch):
  _log(tmp_path, "ask", "repeat", "unlinked")
  _publish(tmp_path, monkeypatch, "run-1", _decision("ask", "asked", "Proposed."))
  joined = {**_decision("repeat", "joined", "Same cause as ask."), "proposal_id": "ask"}
  _publish(tmp_path, monkeypatch, "run-2", joined)
  trail = friction_queue.history(tmp_path, "ask")
  assert [(record.get("friction_id"), record.get("stage")) for record in trail] == [
    ("ask", None), ("repeat", None)]
  assert [entry["id"] for entry in friction_queue.pending(tmp_path)] == ["unlinked"]
  with pytest.raises(ValueError, match="report-backed asked"):
    _publish(tmp_path, monkeypatch, "run-3", {
      **_decision("unlinked", "joined", "Unknown proposal."), "proposal_id": "missing"})
  assert friction_queue.history(tmp_path, "ask") == trail


def test_repeated_observation_is_idempotent_and_current_view_reports_only_evidence(tmp_path, monkeypatch):
  _log(tmp_path, "ask")
  _publish(tmp_path, monkeypatch, "run-1", _decision("ask", "asked", "Proposal."))
  before = friction_queue.proposals(tmp_path)
  assert before[0]["last_observation"] is None
  assert before[0]["open_for_review"] is True
  first = friction_queue.record_proposal(tmp_path, "ask", "activated", "deploy:one", "Observed running version.")
  assert friction_queue.record_proposal(tmp_path, "ask", "activated", "deploy:one", "Saw it again.") == first
  assert len(friction_queue._read_jsonl(tmp_path / "outcomes.jsonl")) == 2
  view = friction_queue.proposals(tmp_path)
  assert view[0]["last_observation"] == first
  assert view[0]["open_for_review"] is True  # Activation is not verification.
  friction_queue.record_proposal(tmp_path, "ask", "verified", "test:live", "Observed live behavior.")
  assert friction_queue.proposals(tmp_path)[0]["open_for_review"] is False


def test_current_proposal_view_reads_ledger_once(tmp_path, monkeypatch):
  _log(tmp_path, "a", "b")
  _publish(tmp_path, monkeypatch, "run-1", _decision("a", "asked", "First."), _decision("b", "asked", "Second."))
  original = friction_queue._read_jsonl
  reads = []
  def read(path):
    reads.append(path)
    return original(path)
  monkeypatch.setattr(friction_queue, "_read_jsonl", read)
  assert len(friction_queue.proposals(tmp_path)) == 2
  assert reads == [tmp_path / "outcomes.jsonl"]


def test_new_linked_friction_reopens_review_after_verification(tmp_path, monkeypatch):
  _log(tmp_path, "ask", "repeat")
  _publish(tmp_path, monkeypatch, "run-1", _decision("ask", "asked", "Proposed."))
  friction_queue.record_proposal(tmp_path, "ask", "verified", "test:live", "Observed live behavior.")
  assert friction_queue.proposals(tmp_path)[0]["open_for_review"] is False
  _publish(tmp_path, monkeypatch, "run-2", {
    **_decision("repeat", "joined", "Same cause recurred."), "proposal_id": "ask"})
  assert friction_queue.proposals(tmp_path)[0]["open_for_review"] is True


def test_malformed_ledger_objects_cannot_crash_proposal_projection(tmp_path, monkeypatch):
  _log(tmp_path, "ask")
  _publish(tmp_path, monkeypatch, "run-1", _decision("ask", "asked", "Proposed."))
  with (tmp_path / "outcomes.jsonl").open("a") as ledger:
    ledger.write(json.dumps({"kind": "proposal_event", "proposal_id": ["ask"]}) + "\n")
    ledger.write(json.dumps({"kind": "proposal_event", "proposal_id": "ask"}) + "\n")
    ledger.write(json.dumps({"outcome": "joined", "proposal_id": "ask", "run": "run-1"}) + "\n")
  assert friction_queue.proposals(tmp_path)[0]["open_for_review"] is True
