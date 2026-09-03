import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

import reflection_floor_brief


class ReflectionFloorBriefTests(unittest.TestCase):
  def test_writes_truthful_notice_and_preserves_existing_state(self):
    with tempfile.TemporaryDirectory() as raw:
      storage = Path(raw)
      (storage / "state.json").write_text(json.dumps({
        "streak": 12,
        "partner_preference": "keep",
      }))

      receipt = reflection_floor_brief.write_floor_brief(
        storage,
        run_date=dt.date(2026, 9, 3),
        reason="timeout",
        exit_code=124,
      )

      report = Path(receipt["brief"]).read_text(encoding="utf-8")
      state = json.loads((storage / "state.json").read_text(encoding="utf-8"))
      self.assertIn('data-reflection-floor="timeout"', report)
      self.assertIn("wall-clock safety boundary", report)
      self.assertEqual(state["streak"], 12)
      self.assertEqual(state["partner_preference"], "keep")
      self.assertEqual(state["last_outcome"]["exit_code"], 124)
      self.assertEqual(state["last_outcome"]["status"], "failed")

  def test_clean_process_without_brief_gets_contract_failure_copy(self):
    with tempfile.TemporaryDirectory() as raw:
      receipt = reflection_floor_brief.write_floor_brief(
        Path(raw),
        run_date=dt.date(2026, 9, 3),
        reason="brief_missing",
        exit_code=68,
      )

      report = Path(receipt["brief"]).read_text(encoding="utf-8")
      self.assertIn("finished without publishing", report)
      self.assertIn("generated outside the agent process", report)

  def test_failed_retry_preserves_an_existing_substantive_same_day_brief(self):
    with tempfile.TemporaryDirectory() as raw:
      storage = Path(raw)
      report = storage / "reports" / "2026-09-03.html"
      report.parent.mkdir()
      report.write_text("<html>valuable earlier reflection</html>", encoding="utf-8")
      (storage / "state.json").write_text('{"streak":4,"keep":"yes"}')

      result = reflection_floor_brief.write_floor_brief(
        storage, run_date=dt.date(2026, 9, 3),
        reason="runner_failed", exit_code=1,
      )

      self.assertFalse(result["written"])
      self.assertEqual(report.read_text(), "<html>valuable earlier reflection</html>")
      self.assertEqual(json.loads((storage / "state.json").read_text()), {
        "streak": 4, "keep": "yes",
      })

  def test_receipt_rejects_unchanged_future_dated_report(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      report = root / "brief.html"
      receipt = root / "receipt.json"
      report.write_text("stale", encoding="utf-8")
      future = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc).timestamp()
      os.utime(report, (future, future))

      reflection_floor_brief.start_report_receipt(receipt, report, "run-1")
      result = reflection_floor_brief.check_report_receipt(receipt, report, "run-1")

      self.assertFalse(result["published_this_run"])

  def test_receipt_rejects_unchanged_same_second_report(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      report = root / "brief.html"
      receipt = root / "receipt.json"
      report.write_text("already here", encoding="utf-8")
      second = int(dt.datetime.now(dt.timezone.utc).timestamp())
      os.utime(report, (second, second))

      reflection_floor_brief.start_report_receipt(receipt, report, "run-same")
      result = reflection_floor_brief.check_report_receipt(
        receipt, report, "run-same",
      )

      self.assertFalse(result["published_this_run"])

  def test_receipt_detects_atomic_publication_even_with_same_second_mtime(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      report = root / "brief.html"
      receipt = root / "receipt.json"
      report.write_text("old", encoding="utf-8")
      second = 1_700_000_000
      os.utime(report, (second, second))
      reflection_floor_brief.start_report_receipt(receipt, report, "run-2")
      replacement = root / "replacement"
      replacement.write_text("new", encoding="utf-8")
      os.utime(replacement, (second, second))
      os.replace(replacement, report)

      result = reflection_floor_brief.check_report_receipt(receipt, report, "run-2")

      self.assertTrue(result["published_this_run"])

  def test_receipt_does_not_treat_metadata_only_touch_as_publication(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      report = root / "brief.html"
      receipt = root / "receipt.json"
      report.write_text("same bytes", encoding="utf-8")
      reflection_floor_brief.start_report_receipt(receipt, report, "run-touch")
      future = dt.datetime(2031, 1, 1, tzinfo=dt.timezone.utc).timestamp()
      os.utime(report, (future, future))

      result = reflection_floor_brief.check_report_receipt(
        receipt, report, "run-touch",
      )

      self.assertFalse(result["published_this_run"])

  def test_failed_retry_restores_the_exact_pre_run_report_bytes(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      report = root / "brief.html"
      receipt = root / "receipt.json"
      original = b"<html>valuable earlier reflection</html>\n"
      report.write_bytes(original)
      reflection_floor_brief.start_report_receipt(receipt, report, "run-restore")
      report.write_bytes(b"<html>partial failed retry</html>")
      self.assertTrue(reflection_floor_brief.check_report_receipt(
        receipt, report, "run-restore",
      )["published_this_run"])

      self.assertTrue(reflection_floor_brief.restore_pre_run_report(
        receipt, report, "run-restore",
      ))
      self.assertEqual(report.read_bytes(), original)
      self.assertFalse(reflection_floor_brief.restore_pre_run_report(
        receipt, report, "run-restore",
      ))

  def test_failed_identical_atomic_republication_is_not_a_new_brief(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      report = root / "brief.html"
      receipt = root / "receipt.json"
      report.write_bytes(b"same exact report")
      reflection_floor_brief.start_report_receipt(receipt, report, "run-identical")
      replacement = root / "replacement"
      replacement.write_bytes(report.read_bytes())
      os.replace(replacement, report)
      self.assertTrue(reflection_floor_brief.check_report_receipt(
        receipt, report, "run-identical",
      )["published_this_run"])

      self.assertTrue(reflection_floor_brief.restore_pre_run_report(
        receipt, report, "run-identical",
      ))
      result = json.loads(receipt.read_text(encoding="utf-8"))
      self.assertFalse(result["published_this_run"])
      self.assertTrue(result["restored_pre_run_report"])
      self.assertFalse(reflection_floor_brief.restore_pre_run_report(
        receipt, report, "run-identical",
      ))

  def test_failed_retry_refuses_a_corrupt_pre_run_snapshot(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      report = root / "brief.html"
      receipt = root / "receipt.json"
      report.write_text("valuable", encoding="utf-8")
      reflection_floor_brief.start_report_receipt(receipt, report, "run-corrupt")
      Path(f"{receipt}.before").write_text("corrupt", encoding="utf-8")
      report.write_text("failed replacement", encoding="utf-8")

      with self.assertRaisesRegex(OSError, "missing or corrupt"):
        reflection_floor_brief.restore_pre_run_report(
          receipt, report, "run-corrupt",
        )
      self.assertEqual(report.read_text(encoding="utf-8"), "failed replacement")

  def test_failed_run_restores_pre_run_streak_without_discarding_new_headline(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      reports = root / "reports"
      reports.mkdir()
      report = reports / "2026-09-03.html"
      receipt = root / "receipt.json"
      (root / "state.json").write_text('{"streak":5,"old":"keep"}')
      reflection_floor_brief.start_report_receipt(receipt, report, "run-3")
      (root / "state.json").write_text(
        '{"streak":6,"old":"keep","last_summary":"partial result"}',
      )

      self.assertTrue(reflection_floor_brief.restore_pre_run_streak(
        receipt, report, "run-3",
      ))
      self.assertEqual(json.loads((root / "state.json").read_text()), {
        "streak": 5, "old": "keep", "last_summary": "partial result",
      })

  def test_substantive_streak_excludes_floor_notices_without_resetting_state(self):
    with tempfile.TemporaryDirectory() as raw:
      storage = Path(raw)
      reports = storage / "reports"
      reports.mkdir()
      (reports / "2026-09-01.html").write_text("<html>substantive</html>")
      (storage / "reflection-run-metrics.jsonl").write_text(json.dumps({
        "started_at": "2026-09-01T05:00:00+00:00", "exit_code": 0,
        "dry_run": False, "brief_written": True, "brief_source": "agent",
      }) + "\n")
      reflection_floor_brief.write_floor_brief(
        storage, run_date=dt.date(2026, 9, 2), reason="timeout", exit_code=124,
      )
      state = json.loads((storage / "state.json").read_text())
      state["streak"] = 7
      (storage / "state.json").write_text(json.dumps(state))

      # A second floor write preserves the displayed prior streak.
      reflection_floor_brief.write_floor_brief(
        storage, run_date=dt.date(2026, 9, 2), reason="timeout", exit_code=124,
      )
      self.assertEqual(json.loads((storage / "state.json").read_text())["streak"], 7)
      (reports / "2026-09-03.html").write_text("<html>substantive again</html>")

      self.assertEqual(
        reflection_floor_brief.record_substantive_success(
          storage, dt.date(2026, 9, 3),
        ),
        1,
      )


if __name__ == "__main__":
  unittest.main()
