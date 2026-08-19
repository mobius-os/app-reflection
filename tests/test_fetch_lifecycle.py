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
      inputs = root / "apps" / "reflection" / "inputs"
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


if __name__ == "__main__":
  unittest.main()
