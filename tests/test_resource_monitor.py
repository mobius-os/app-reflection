import datetime as dt
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import resource_monitor


class ResourceMonitorTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name)
    self.data = self.root / "data"
    self.data.mkdir()
    self.history = self.root / "resource-history.jsonl"
    self.state = self.root / "resource-state.json"

  def tearDown(self):
    self.temp.cleanup()

  def test_first_snapshot_runs_deep_scan_and_categories_data(self):
    profile = self.data / "agent-browser-profiles" / "qa-profile" / "Default" / "Cache"
    profile.mkdir(parents=True)
    (profile / "cache.bin").write_bytes(b"x" * 1024)
    db = self.data / "db"
    db.mkdir()
    (db / "ultimate.db").write_bytes(b"d" * 512)

    snapshot, state = resource_monitor.make_snapshot(
      self.data, history_path=self.history, state_path=self.state,
      now=dt.datetime(2026, 7, 17, tzinfo=dt.timezone.utc),
    )

    self.assertTrue(snapshot["deep_scan"]["ran"])
    self.assertEqual(snapshot["deep_scan"]["reason"], "first-scan")
    self.assertTrue(snapshot["deep_scan"]["complete"])
    self.assertEqual(snapshot["deep_scan"]["browser_profiles"]["count"], 1)
    self.assertGreater(snapshot["deep_scan"]["browser_profiles"]["known_cache_bytes"], 0)
    self.assertIn("last_complete_deep_scan_at", state)

  def test_recent_healthy_snapshot_skips_recursive_scan(self):
    now = dt.datetime(2026, 7, 17, tzinfo=dt.timezone.utc)
    used = shutil.disk_usage(self.data).used
    self.history.write_text(json.dumps({"disk": {"used_bytes": used}}) + "\n")
    self.state.write_text(json.dumps({
      "last_complete_deep_scan_at": (now - dt.timedelta(days=1)).isoformat(),
    }))

    snapshot, _ = resource_monitor.make_snapshot(
      self.data, history_path=self.history, state_path=self.state, now=now,
    )

    self.assertFalse(snapshot["deep_scan"]["ran"])
    self.assertEqual(snapshot["deep_scan"]["reason"], "not-due")

  def test_record_command_writes_structured_ledger(self):
    ledger = self.root / "decisions.jsonl"
    rc = resource_monitor.main([
      "record", "--ledger", str(ledger), "--area", "browser-profiles",
      "--evidence", "2.8 GiB with 1.0 GiB cache", "--action", "pruned closed caches",
      "--result", "usage fell below the low-water mark",
      "--next-review-at", "2026-07-24T00:00:00+00:00",
      "--review-trigger", "disk pressure or profile bytes exceed 2 GiB",
      "--bytes-reclaimed", "1073741824",
    ])

    self.assertEqual(rc, 0)
    row = json.loads(ledger.read_text().strip())
    self.assertEqual(row["area"], "browser-profiles")
    self.assertEqual(row["bytes_reclaimed"], 1073741824)
    self.assertIn("next_review_at", row)


if __name__ == "__main__":
  unittest.main()
