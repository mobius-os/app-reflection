import json
import tempfile
import unittest
from pathlib import Path

import effort_summary


class EffortSummaryTests(unittest.TestCase):
  def test_rolling_ratio_weights_runs_and_includes_reported_cache_tokens(self):
    with tempfile.TemporaryDirectory() as raw:
      path = Path(raw) / "metrics.jsonl"
      rows = [
        {"exit_code": 0, "dry_run": False, "started_at": "one", "model_usage": {
          "attempts": [{"usage": {
            "input_tokens": 10, "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 5, "output_tokens": 5,
          }}],
          "work_context": {"chat_agent_work": {"total_tokens": 1000}},
        }},
        {"exit_code": 0, "dry_run": False, "started_at": "two", "model_usage": {
          "attempts": [{"usage": {"total_tokens": 100}}],
          "work_context": {"chat_agent_work": {"total_tokens": 3000}},
        }},
        {"exit_code": 0, "dry_run": True, "model_usage": {
          "attempts": [{"usage": {"total_tokens": 999}}],
          "work_context": {"chat_agent_work": {"total_tokens": 999}},
        }},
      ]
      path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
      result = effort_summary.build(path)
      self.assertEqual(result["window_runs"], 2)
      self.assertEqual(result["reflection_tokens"], 200)
      self.assertEqual(result["foreground_tokens"], 4000)
      self.assertEqual(result["average_token_ratio"], 0.05)

  def test_missing_comparable_receipts_is_explicit(self):
    with tempfile.TemporaryDirectory() as raw:
      result = effort_summary.build(Path(raw) / "missing.jsonl")
      self.assertEqual(result["window_runs"], 0)
      self.assertIsNone(result["average_token_ratio"])


if __name__ == "__main__":
  unittest.main()
