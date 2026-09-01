import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import interview_outcomes


class InterviewOutcomeTests(unittest.TestCase):
  def test_keeps_verified_outcomes_and_followups(self):
    with tempfile.TemporaryDirectory() as raw:
      path = Path(raw) / "outcomes.jsonl"
      path.write_text(json.dumps({
        "subject_id": "chat-1", "subject_kind": "chat",
        "method": "interview", "verification": "verified",
        "outcome": "Fixed the owning parser.",
        "evidence": ["apps/foo/parser.py: parse_record"],
        "next_action": "Watch the next scheduled run.",
        "provider": "codex",
        "source_session_id": "source-session",
        "forked_session_id": "forked-session",
        "exact_session_fork": True,
      }) + "\n")
      result = interview_outcomes.build_status(path)
      self.assertEqual(result["valid_outcomes"], 1)
      self.assertEqual(result["methods"], {"interview": 1})
      self.assertEqual(result["followups"][0]["subject_id"], "chat-1")
      self.assertTrue(result["outcomes"][0]["exact_session_fork"])

  def test_interview_requires_an_exact_distinct_provider_fork_receipt(self):
    row = {
      "subject_id": "chat-1", "subject_kind": "chat",
      "method": "interview", "verification": "unverified",
      "outcome": "A plausible but unproven interview.", "evidence": [],
    }

    self.assertIsNone(interview_outcomes.normalize(row))
    row.update({
      "provider": "claude",
      "source_session_id": "same-session",
      "forked_session_id": "same-session",
      "exact_session_fork": True,
    })
    self.assertIsNone(interview_outcomes.normalize(row))

  def test_unavailable_interview_is_a_first_class_honest_disposition(self):
    row = interview_outcomes.normalize({
      "subject_id": "chat-1", "subject_kind": "chat",
      "method": "interview_unavailable", "verification": "unverified",
      "outcome": "Exact-session coaching did not complete.",
      "reason": "Provider fork returned only a preamble.", "evidence": [],
    })

    self.assertIsNotNone(row)
    self.assertEqual(row["method"], "interview_unavailable")

  def test_verified_requires_evidence_and_invalid_is_visible(self):
    with tempfile.TemporaryDirectory() as raw:
      path = Path(raw) / "outcomes.jsonl"
      path.write_text(json.dumps({
        "subject_id": "chat-1", "subject_kind": "chat",
        "method": "interview", "verification": "verified",
        "outcome": "Claim without proof.", "evidence": [],
      }) + "\nnot-json\n")
      result = interview_outcomes.build_status(path)
      self.assertEqual(result["valid_outcomes"], 0)
      self.assertEqual(result["invalid_outcomes"], 2)

  def test_coverage_exposes_missing_and_duplicate_subjects(self):
    with tempfile.TemporaryDirectory() as raw:
      path = Path(raw) / "outcomes.jsonl"
      row = {
        "subject_id": "chat-1", "subject_kind": "chat",
        "method": "summary_sufficient", "verification": "not_applicable",
        "outcome": "Routine work was fully captured by the summary.",
        "evidence": [],
      }
      path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
      result = interview_outcomes.build_status(
        path, expected_ids=["chat-1", "chat-2"],
      )
      self.assertFalse(result["coverage"]["complete"])
      self.assertEqual(result["coverage"]["missing_subject_ids"], ["chat-2"])
      self.assertEqual(result["coverage"]["duplicate_subject_ids"], ["chat-1"])

  def test_invalid_expected_subject_source_fails_closed(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      ledger = root / "outcomes.jsonl"
      expected = root / "chats-status.json"
      output = root / "status.json"
      ledger.write_text("")
      expected.write_text("not-json")
      result = subprocess.run([
        sys.executable, str(Path(interview_outcomes.__file__)),
        "--ledger", str(ledger), "--output", str(output),
        "--expected-subjects", str(expected),
      ], check=False)
      self.assertEqual(result.returncode, 1)
      status = json.loads(output.read_text())
      self.assertFalse(status["expected_subjects_source_valid"])

  def test_valid_empty_expected_subject_source_is_complete(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      ledger = root / "outcomes.jsonl"
      expected = root / "chats-status.json"
      output = root / "status.json"
      ledger.write_text("")
      expected.write_text(json.dumps({"subject_ids": []}))
      result = subprocess.run([
        sys.executable, str(Path(interview_outcomes.__file__)),
        "--ledger", str(ledger), "--output", str(output),
        "--expected-subjects", str(expected),
      ], check=False)
      self.assertEqual(result.returncode, 0)
      status = json.loads(output.read_text())
      self.assertTrue(status["expected_subjects_source_valid"])


if __name__ == "__main__":
  unittest.main()
