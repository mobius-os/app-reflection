import json
import tempfile
import unittest
from pathlib import Path

import learning_loop


class LearningLoopTests(unittest.TestCase):
  def test_composes_orientation_without_scoring(self):
    with tempfile.TemporaryDirectory() as raw:
      inputs = Path(raw)
      (inputs / "activity-status.json").write_text(json.dumps({"ok": True, "event_count": 8}))
      (inputs / "chats-status.json").write_text(json.dumps({"active_ok": True, "deleted_complete": True}))
      (inputs / "memory-health.json").write_text(json.dumps({
        "needs_attention": False, "reasons": [],
        "recall_activity": {"chat_days": 2},
        "recall_hindsight": {"hindsight_assessed": 1},
      }))
      (inputs / "experiment-status.json").write_text(json.dumps({"active": [{}], "due": []}))
      (inputs / "latest-effort.json").write_text(json.dumps({
        "exit_code": 0, "duration_seconds": 30,
        "model_usage": {"reported_cost_usd": 1.25, "attempts": [
          {"usage": {"input_tokens": 4, "output_tokens": 2}},
        ]},
      }))
      result = learning_loop.build(inputs)
      self.assertIn("not objectives", result["purpose"])
      self.assertNotIn("score", result)
      self.assertEqual(result["learning_state"]["recall_chats"], 2)
      self.assertEqual(result["learning_state"]["active_experiments"], 1)
      self.assertEqual(result["prior_effort"]["reported_cost_usd"], 1.25)
      self.assertEqual(result["prior_effort"]["token_usage"]["output_tokens"], 2)


if __name__ == "__main__":
  unittest.main()
