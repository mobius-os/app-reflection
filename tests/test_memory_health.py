import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import memory_health


class MemoryHealthTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name) / "memory"
    (self.root / "app-state" / "run-log").mkdir(parents=True)
    (self.root / "repository").mkdir()

  def tearDown(self):
    self.temp.cleanup()

  def _runs(self, *rows):
    path = self.root / "app-state" / "run-log" / "2026-07-20.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

  def test_recovered_failure_is_visible_without_triggering_shared_writes(self):
    self._runs(
      {
        "status": "failed", "run_id": "failed", "error_code": "unverified_chat_provenance",
        "finished_at": "2026-07-19T05:31:00+00:00", "source_chat_count": 30,
      },
      {
        "status": "published", "run_id": "recovered", "provider": "codex",
        "finished_at": "2026-07-20T05:36:00+00:00",
      },
    )
    (self.root / "repository" / "graph.json").write_text(json.dumps({
      "nodes": [{}, {}], "edges": [{}],
      "problems": [{"severity": "warning", "type": "oversized_note"}],
    }))

    health = memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

    self.assertFalse(health["needs_attention"])
    self.assertTrue(health["recovered_after_failure"])
    self.assertIn("recovered_after_recent_failure", health["advisories"])
    self.assertEqual(health["latest_graph"]["warnings"], 1)
    self.assertFalse(health["writer_contract"]["reflection_may_write_graph"])
    self.assertNotIn("summary", health["last_run"])

  def test_repeated_failures_escalate_and_report_pending_retry_count(self):
    self._runs(
      {"status": "failed", "finished_at": "2026-07-19T05:30:00+00:00"},
      {
        "status": "degraded", "finished_at": "2026-07-20T05:30:00+00:00",
        "attempted_agents": [{"rejection_code": "unverified_chat_provenance"}],
      },
    )
    pending = self.root / "app-state" / "pending-chat-ids.json"
    pending.write_text(json.dumps({"schema": 1, "chat_ids": ["one", "two"]}))

    health = memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

    self.assertTrue(health["needs_attention"])
    self.assertEqual(health["consecutive_unsuccessful_runs"], 2)
    self.assertEqual(health["pending_chat_count"], 2)
    self.assertIn("repeated_unsuccessful_runs", health["reasons"])
    self.assertEqual(
      health["last_rejection_codes"], ["unverified_chat_provenance"],
    )

  def test_canonical_running_status_is_not_hidden_by_prior_publish_history(self):
    self._runs({
      "status": "published", "run_id": "yesterday",
      "finished_at": "2026-07-19T05:35:00+00:00",
    })
    status = self.root / "app-state" / "run-status.json"
    status.write_text(json.dumps({
      "status": "running", "run_id": "today",
      "started_at": "2026-07-20T05:30:00+00:00",
    }))

    health = memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

    self.assertEqual(health["last_run"]["status"], "running")
    self.assertTrue(health["needs_attention"])
    self.assertIn("latest_run_still_running", health["reasons"])

  def test_stale_status_file_does_not_hide_a_newer_terminal_run(self):
    self._runs({
      "status": "published", "run_id": "newer",
      "finished_at": "2026-07-20T05:35:00+00:00",
    })
    status = self.root / "app-state" / "run-status.json"
    status.write_text(json.dumps({
      "status": "failed", "run_id": "stale",
      "finished_at": "2026-07-19T05:35:00+00:00",
    }))

    health = memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

    self.assertEqual(health["last_run"]["run_id"], "newer")
    self.assertFalse(health["needs_attention"])

  def test_full_retry_queue_requires_attention(self):
    self._runs({
      "status": "published", "finished_at": "2026-07-20T05:35:00+00:00",
    })
    pending = self.root / "app-state" / "pending-chat-ids.json"
    pending.write_text(json.dumps({
      "schema": 1, "capacity": 500,
      "chat_ids": [f"chat-{index}" for index in range(500)],
    }))

    health = memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

    self.assertTrue(health["needs_attention"])
    self.assertIn("pending_chat_queue_at_capacity", health["reasons"])


if __name__ == "__main__":
  unittest.main()
