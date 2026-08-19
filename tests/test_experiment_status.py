import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import experiment_status


class ExperimentStatusTests(unittest.TestCase):
  def test_latest_event_owns_lifecycle_and_due_is_time_only(self):
    with tempfile.TemporaryDirectory() as raw:
      ledger = Path(raw) / "experiments.jsonl"
      rows = [
        {
          "experiment_id": "deeper-memory-interviews",
          "recorded_at": "2026-08-10T06:00:00Z",
          "area": "reflection",
          "status": "running",
          "hypothesis": "One deep interview produces better repairs.",
          "review_after": "2026-08-16T06:00:00Z",
          "review_trigger": "Review after another busy night.",
        },
        {
          "experiment_id": "deeper-memory-interviews",
          "recorded_at": "2026-08-16T06:10:00Z",
          "area": "reflection",
          "status": "concluded",
          "result": "The deeper interview found the owning parser defect.",
        },
        {
          "experiment_id": "recall-parent-cue",
          "recorded_at": "2026-08-15T05:30:00Z",
          "area": "memory",
          "status": "running",
          "expected_signal": "A later recall is helpful without deeper search.",
          "review_after": "2026-08-17T00:00:00Z",
        },
      ]
      ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

      result = experiment_status.build_status(
        ledger,
        now=dt.datetime(2026, 8, 17, 1, tzinfo=dt.timezone.utc),
      )

      self.assertEqual(result["experiment_count"], 2)
      self.assertEqual(
        [row["experiment_id"] for row in result["active"]],
        ["recall-parent-cue"],
      )
      self.assertEqual(result["due"], result["active"])

  def test_malformed_events_are_counted_without_hiding_valid_work(self):
    with tempfile.TemporaryDirectory() as raw:
      ledger = Path(raw) / "experiments.jsonl"
      ledger.write_text(
        "not json\n"
        + json.dumps({"experiment_id": "Bad ID", "status": "running"}) + "\n"
        + json.dumps({
          "experiment_id": "valid", "status": "proposed",
          "review_trigger": "When the next recall lands.",
        }) + "\n"
      )

      result = experiment_status.build_status(ledger)

      self.assertEqual(result["valid_event_count"], 1)
      self.assertEqual(result["invalid_event_count"], 2)
      self.assertEqual(result["active"][0]["experiment_id"], "valid")
      self.assertEqual(result["due"], [])


if __name__ == "__main__":
  unittest.main()
