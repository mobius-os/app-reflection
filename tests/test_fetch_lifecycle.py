import fcntl
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


FETCH = Path(__file__).resolve().parents[1] / "fetch.sh"


class FetchLifecycleTests(unittest.TestCase):
  def test_invalid_app_id_fails_before_creating_runtime_state(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      result = subprocess.run(
        ["bash", str(FETCH), "not-an-id"],
        env={**os.environ, "DATA_DIR": str(root)},
        capture_output=True,
        text=True,
        check=False,
      )
      self.assertEqual(result.returncode, 2)
      self.assertIn("numeric app id required", result.stderr)
      self.assertFalse((root / "apps").exists())

  def test_overlapping_run_does_not_clear_active_run_inputs(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      logs = root / "cron-logs"
      inputs = root / "apps" / "57" / "inputs"
      logs.mkdir(parents=True)
      inputs.mkdir(parents=True)
      sentinels = [
        inputs / "model-usage.json",
        inputs / "prev-report.html",
        inputs / "prev-report-name.txt",
        inputs / "prev-question-answers.json",
      ]
      for path in sentinels:
        path.write_text("active-run-state")
      lock_path = logs / "reflection.lock"
      with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
          ["bash", str(FETCH), "57"],
          env={**os.environ, "DATA_DIR": str(root)},
          capture_output=True,
          text=True,
          check=False,
        )
      self.assertEqual(result.returncode, 5)
      self.assertTrue(all(path.read_text() == "active-run-state" for path in sentinels))

  def test_legacy_run_receipts_import_once_without_overwriting_canonical_data(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      logs = root / "cron-logs"
      legacy = root / "apps" / "reflection" / "runs" / "2026-09-06"
      canonical = root / "apps" / "57" / "runs" / "2026-09-06"
      logs.mkdir(parents=True)
      legacy.mkdir(parents=True)
      canonical.mkdir(parents=True)
      (legacy / "kept.jsonl").write_text("legacy receipt")
      (legacy / "conflict.jsonl").write_text("legacy value")
      (canonical / "conflict.jsonl").write_text("canonical value")

      # Imports are deliberately behind the wrapper's no-overlap lock. Dry mode
      # exercises that owned migration without starting a model session.
      (root / "service-token.txt").write_text("test-token")
      result = subprocess.run(
        ["bash", str(FETCH), "57"],
        env={
          **os.environ,
          "DATA_DIR": str(root),
          "REFLECTION_DRY": "1",
          "REFLECTION_RESOURCE_WARN_PERCENT": "100",
          "REFLECTION_RESOURCE_CRITICAL_PERCENT": "101",
          "API_BASE_URL": "http://127.0.0.1:1",
        },
        capture_output=True,
        text=True,
        check=False,
      )
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertEqual((canonical / "kept.jsonl").read_text(), "legacy receipt")
      self.assertEqual((canonical / "conflict.jsonl").read_text(), "canonical value")
      self.assertTrue((root / "apps" / "57" / ".legacy-runs-imported-v1").is_file())
      self.assertEqual((legacy / "kept.jsonl").read_text(), "legacy receipt")


if __name__ == "__main__":
  unittest.main()
