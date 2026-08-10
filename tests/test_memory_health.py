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

  def _reads(self, **per_day):
    """Write `count` reads from `count` distinct chats into read-log/<day>.jsonl."""
    log = self.root / "app-state" / "read-log"
    log.mkdir(parents=True, exist_ok=True)
    for day, count in per_day.items():
      (log / f"{day.replace('_', '-')}.jsonl").write_text(
        "".join(
          json.dumps({
            "at": f"{day.replace('_', '-')}T01:00:00Z",
            "read_id": f"{day}-{i}",
            "chat_id": f"chat-{day}-{i}",
          }) + "\n"
          for i in range(count)
        )
      )

  def _health_on_20th(self):
    return memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

  def test_recall_activity_resumes_at_checkpoint_and_orders_every_day(self):
    self._runs({"status": "published", "finished_at": "2026-07-20T05:36:00+00:00"})
    self._reads(**{
      "2026_07_11": 8,
      "2026_07_12": 2,
      "2026_07_14": 3,
      "2026_07_19": 1,
    })

    health = memory_health.build_health(
      self.root,
      now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
      since="2026-07-12T00:00:00Z",
    )

    recall = health["recall_activity"]
    self.assertEqual(recall["days"][0], {"date": "2026-07-12", "chats": 2})
    self.assertEqual(recall["days"][-1], {"date": "2026-07-20", "chats": 0})
    self.assertEqual(
      [day["date"] for day in recall["days"]],
      [f"2026-07-{day:02}" for day in range(12, 21)],
    )
    self.assertEqual(recall["chat_days"], 6)
    self.assertNotIn("recall_collapsed", health["reasons"])

  def test_missing_read_log_reports_zeroes_without_failing(self):
    self._runs({"status": "published", "finished_at": "2026-07-20T05:36:00+00:00"})

    health = self._health_on_20th()

    self.assertEqual(health["recall_activity"]["days"], [])
    self.assertEqual(health["recall_activity"]["chat_days"], 0)
    self.assertNotIn("recall_collapsed", health["reasons"])

  def test_read_history_before_the_checkpoint_is_ignored(self):
    self._runs({"status": "published", "finished_at": "2026-07-20T05:36:00+00:00"})
    self._reads(**{"2026_06_01": 30})

    health = memory_health.build_health(
      self.root,
      now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
      since="2026-07-19T00:00:00Z",
    )

    self.assertEqual(health["recall_activity"]["days"], [
      {"date": "2026-07-19", "chats": 0},
      {"date": "2026-07-20", "chats": 0},
    ])

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

  def test_queue_progress_is_a_validated_content_free_outcome(self):
    self._runs({
      "status": "published",
      "finished_at": "2026-07-20T05:30:00+00:00",
      "chat_queue_progress": {
        "pending_before_ack": 10,
        "acknowledged": 4,
        "remaining": 6,
      },
    })

    health = memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

    self.assertEqual(health["queue_progress"], {
      "pending_before_ack": 10,
      "acknowledged": 4,
      "remaining": 6,
    })
    self.assertNotIn("chat_queue_progress", health["last_run"])

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
    self.assertEqual(
      health["latest_terminal_run"]["run_id"], "yesterday",
    )
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

  def test_current_terminal_status_wins_when_log_append_is_missing(self):
    self._runs({
      "status": "published", "run_id": "yesterday",
      "finished_at": "2026-07-19T05:35:00+00:00",
    })
    status_path = self.root / "app-state" / "run-status.json"

    for terminal_status in ("failed", "abandoned"):
      with self.subTest(terminal_status=terminal_status):
        status_path.write_text(json.dumps({
          "status": terminal_status, "run_id": f"today-{terminal_status}",
          "finished_at": "2026-07-20T05:35:00+00:00",
        }))

        health = memory_health.build_health(
          self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(
          health["latest_terminal_run"]["run_id"], f"today-{terminal_status}",
        )
        self.assertEqual(health["consecutive_unsuccessful_runs"], 1)
        self.assertIn("latest_run_unsuccessful", health["reasons"])
        if terminal_status == "failed":
          self.assertEqual(health["last_failure"]["run_id"], "today-failed")

  def test_current_publish_supplies_all_metrics_when_log_append_is_missing(self):
    self._runs({
      "status": "failed", "run_id": "yesterday",
      "finished_at": "2026-07-19T05:35:00+00:00",
    })
    (self.root / "app-state" / "run-status.json").write_text(json.dumps({
      "status": "published", "run_id": "today",
      "finished_at": "2026-07-20T05:35:00+00:00",
    }))

    health = memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

    self.assertEqual(health["latest_terminal_run"]["run_id"], "today")
    self.assertEqual(health["consecutive_unsuccessful_runs"], 0)
    self.assertNotIn("no_published_run_observed", health["reasons"])
    self.assertNotIn("publish_stale", health["reasons"])
    self.assertFalse(health["needs_attention"])

  def test_current_terminal_receipt_replaces_same_run_in_log(self):
    self._runs({
      "status": "failed", "run_id": "same-run",
      "finished_at": "2026-07-20T05:34:00+00:00",
    })
    (self.root / "app-state" / "run-status.json").write_text(json.dumps({
      "status": "failed", "run_id": "same-run", "error_code": "latest",
      "finished_at": "2026-07-20T05:35:00+00:00",
    }))

    health = memory_health.build_health(
      self.root, now=dt.datetime(2026, 7, 20, 6, tzinfo=dt.timezone.utc),
    )

    self.assertEqual(health["consecutive_unsuccessful_runs"], 1)
    self.assertEqual(health["last_failure"]["error_code"], "latest")

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
