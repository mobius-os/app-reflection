import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import resource_monitor


class ResourceMonitorTests(unittest.TestCase):
  def setUp(self):
    self.env = mock.patch.dict("os.environ", {
      "REFLECTION_RESOURCE_WARN_PERCENT": "100",
      "REFLECTION_RESOURCE_CRITICAL_PERCENT": "101",
    })
    self.env.start()
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name)
    self.data = self.root / "data"
    self.data.mkdir()
    self.history = self.root / "resource-history.jsonl"
    self.state = self.root / "resource-state.json"

  def tearDown(self):
    self.temp.cleanup()
    self.env.stop()

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
    disk = resource_monitor._filesystem_snapshot(self.data, scope="data-volume")
    self.history.write_text(json.dumps({"disk": disk}) + "\n")
    self.state.write_text(json.dumps({
      "last_complete_deep_scan_at": (now - dt.timedelta(days=1)).isoformat(),
    }))

    snapshot, _ = resource_monitor.make_snapshot(
      self.data, history_path=self.history, state_path=self.state, now=now,
    )

    self.assertFalse(snapshot["deep_scan"]["ran"])
    self.assertEqual(snapshot["deep_scan"]["reason"], "not-due")

  def test_changed_filesystem_identity_never_emits_a_growth_delta(self):
    previous = {
      "scope": "data-volume", "path": "/data", "device_id": 7,
      "total_bytes": 1000, "used_bytes": 900,
    }
    current = {
      "scope": "data-volume", "path": "/data", "device_id": 8,
      "total_bytes": 2000, "used_bytes": 100,
    }

    trend = resource_monitor._trend(previous, current)

    self.assertFalse(trend["comparable_to_previous"])
    self.assertEqual(trend["reason"], "filesystem-identity-changed")
    self.assertIsNone(trend["used_bytes_delta"])

  def test_snapshot_keeps_data_volume_and_container_root_separate(self):
    snapshot, _ = resource_monitor.make_snapshot(
      self.data,
      history_path=self.history,
      state_path=self.state,
      runtime_root=self.root,
      now=dt.datetime(2026, 7, 17, tzinfo=dt.timezone.utc),
    )

    filesystems = snapshot["filesystems"]
    self.assertEqual(filesystems["data_volume"]["scope"], "data-volume")
    self.assertEqual(filesystems["data_volume"]["path"], str(self.data))
    self.assertEqual(filesystems["container_root"]["scope"], "container-root")
    self.assertEqual(filesystems["container_root"]["path"], str(self.root))

  def test_root_pressure_survives_an_unrelated_data_volume_and_identity_change(self):
    now = dt.datetime(2026, 7, 17, tzinfo=dt.timezone.utc)
    previous_data = {
      "scope": "data-volume", "path": "/data", "device_id": 1,
      "total_bytes": 1000, "used_bytes": 200, "free_bytes": 800,
      "used_percent": 20,
    }
    previous_root = {
      "scope": "container-root", "path": "/", "device_id": 2,
      "total_bytes": 1000, "used_bytes": 700, "free_bytes": 300,
      "used_percent": 70,
    }
    self.history.write_text(json.dumps({
      "disk": previous_data,
      "filesystems": {"container_root": previous_root},
    }) + "\n")
    self.state.write_text(json.dumps({
      "last_complete_deep_scan_at": (now - dt.timedelta(days=1)).isoformat(),
    }))
    current_root = {
      **previous_root, "device_id": 3, "used_bytes": 900,
      "free_bytes": 100, "used_percent": 90,
    }

    with mock.patch.object(
      resource_monitor, "_filesystem_snapshot",
      side_effect=[previous_data, current_root],
    ), mock.patch.dict("os.environ", {
      "REFLECTION_RESOURCE_WARN_PERCENT": "80",
      "REFLECTION_RESOURCE_CRITICAL_PERCENT": "85",
    }):
      snapshot, _ = resource_monitor.make_snapshot(
        self.data, history_path=self.history, state_path=self.state,
        runtime_root=self.root, now=now,
      )

    self.assertEqual(snapshot["pressure"], "critical")
    self.assertEqual(snapshot["filesystems"]["data_volume"]["pressure"], "normal")
    self.assertEqual(snapshot["filesystems"]["container_root"]["pressure"], "critical")
    self.assertFalse(snapshot["trend"]["container_root"]["comparable_to_previous"])
    self.assertIsNone(snapshot["trend"]["container_root"]["used_bytes_delta"])

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
