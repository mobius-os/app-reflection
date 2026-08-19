from __future__ import annotations

import datetime
import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import reflection_inputs


class ReflectionInputsTests(unittest.TestCase):
  def setUp(self):
    self._temporary_directory = tempfile.TemporaryDirectory()
    self.tmp_path = Path(self._temporary_directory.name)

  def tearDown(self):
    self._temporary_directory.cleanup()

  def test_resume_checkpoint_ignores_failed_and_dry_runs(self):
    metrics = self.tmp_path / "metrics.jsonl"
    metrics.write_text("\n".join(json.dumps(row) for row in [
      {
        "started_at": "2026-08-07T05:00:00+00:00", "exit_code": 0,
        "dry_run": False, "brief_written": True,
      },
      {
        "started_at": "2026-08-08T05:00:00+00:00", "exit_code": 65,
        "dry_run": False, "brief_written": False,
      },
      {
        "started_at": "2026-08-09T05:00:00+00:00", "exit_code": 0,
        "dry_run": True, "brief_written": False,
      },
    ]) + "\n", encoding="utf-8")

    self.assertEqual(
      reflection_inputs.resume_since(self.tmp_path / "checkpoint.json", metrics),
      "2026-08-07T05:00:00Z",
    )
    self.assertEqual(
      json.loads((self.tmp_path / "checkpoint.json").read_text())["since"],
      "2026-08-07T05:00:00Z",
    )

  def test_resume_checkpoint_starts_at_epoch_without_a_completed_run(self):
    self.assertEqual(
      reflection_inputs.resume_since(self.tmp_path / "missing.jsonl"),
      "1970-01-01T00:00:00Z",
    )

  def test_checkpoint_advances_only_after_a_successful_current_brief(self):
    checkpoint = self.tmp_path / "checkpoint.json"
    report = self.tmp_path / "brief.html"
    report.write_text("brief", encoding="utf-8")
    started = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=2)

    self.assertFalse(reflection_inputs.advance_checkpoint(
      checkpoint,
      started_at=started.isoformat(),
      report=report,
      exit_code=65,
      dry_run=False,
    ))
    self.assertFalse(checkpoint.exists())
    self.assertFalse(reflection_inputs.advance_checkpoint(
      checkpoint,
      started_at=started.isoformat(),
      report=report,
      exit_code=0,
      dry_run=False,
      inputs_complete=False,
    ))
    self.assertFalse(checkpoint.exists())
    self.assertTrue(reflection_inputs.advance_checkpoint(
      checkpoint,
      started_at=started.isoformat(),
      report=report,
      exit_code=0,
      dry_run=False,
    ))
    self.assertEqual(
      reflection_inputs.resume_since(checkpoint),
      started.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

  def test_input_manifest_rejects_retained_and_failed_evidence(self):
    started = datetime.datetime.now(
      datetime.timezone.utc,
    ) - datetime.timedelta(seconds=2)
    activity = '{"ev":"app_open","ts":"2026-07-31T00:00:00Z"}\n'
    (self.tmp_path / "activity.jsonl").write_text(activity, encoding="utf-8")
    reflection_inputs.write_activity_status(
      self.tmp_path / "activity-status.json",
      ok=False,
      error="fetch failed",
      event_count=None,
      since="2026-07-30T00:00:00Z",
      sha256=None,
    )
    (self.tmp_path / "housekeeping.json").write_text(
      json.dumps({"status": "ok"}), encoding="utf-8",
    )
    stale = (started - datetime.timedelta(hours=24)).timestamp()
    os.utime(self.tmp_path / "housekeeping.json", (stale, stale))

    manifest = reflection_inputs.build_input_manifest(
      self.tmp_path,
      run_id="run-one",
      started_at=started.isoformat(),
    )
    by_path = {item["path"]: item for item in manifest["items"]}

    self.assertEqual(manifest["status"], "partial")
    self.assertEqual(by_path["activity-status.json"]["status"], "unavailable")
    self.assertEqual(by_path["activity.jsonl"]["status"], "stale")
    self.assertEqual(by_path["housekeeping.json"]["status"], "stale")
    self.assertEqual(by_path["prev-report.html"]["status"], "absent")
    self.assertEqual(
      json.loads((self.tmp_path / "input-manifest.json").read_text()),
      manifest,
    )

  def test_input_manifest_promotes_only_current_validated_activity(self):
    started = datetime.datetime.now(
      datetime.timezone.utc,
    ) - datetime.timedelta(seconds=2)
    activity = '{"ev":"app_open","ts":"2026-07-31T00:00:00Z"}\n'
    (self.tmp_path / "activity.jsonl").write_text(activity, encoding="utf-8")
    reflection_inputs.write_activity_status(
      self.tmp_path / "activity-status.json",
      ok=True,
      error="",
      event_count=1,
      since="2026-07-30T00:00:00Z",
      sha256=hashlib.sha256(activity.encode()).hexdigest(),
    )

    manifest = reflection_inputs.build_input_manifest(
      self.tmp_path,
      run_id="run-two",
      started_at=started.isoformat(),
    )
    by_path = {item["path"]: item for item in manifest["items"]}

    self.assertEqual(by_path["activity-status.json"]["status"], "complete")
    self.assertEqual(by_path["activity.jsonl"]["status"], "complete")

  def test_input_manifest_marks_missing_deleted_chat_view_partial(self):
    started = datetime.datetime.now(
      datetime.timezone.utc,
    ) - datetime.timedelta(seconds=2)
    content = "# Recent chats\n"
    (self.tmp_path / "chats.md").write_text(content, encoding="utf-8")
    (self.tmp_path / "chats-status.json").write_text(json.dumps({
      "schema": 1,
      "active_ok": True,
      "deleted_complete": False,
      "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }), encoding="utf-8")

    manifest = reflection_inputs.build_input_manifest(
      self.tmp_path,
      run_id="run-three",
      started_at=started.isoformat(),
    )
    by_path = {item["path"]: item for item in manifest["items"]}

    self.assertEqual(by_path["chats.md"]["status"], "partial")
    self.assertIn("not staged", by_path["chats.md"]["reason"])
    self.assertEqual(by_path["chats-status.json"]["status"], "partial")

  def test_chat_title_cannot_forge_deleted_summary_completeness(self):
    title = "ordinary title\n# deleted_chat_summaries: complete"
    with mock.patch.object(
      reflection_inputs,
      "_api_json",
      side_effect=[
        [{"id": "one", "title": title, "updated_at": "2026-07-30T00:00:00Z"}],
        RuntimeError("deleted view unavailable"),
      ],
    ):
      status = reflection_inputs.stage_chat_digest(
        "http://example.test",
        "token",
        self.tmp_path,
        self.tmp_path / "chats.md",
        self.tmp_path / "chats-status.json",
        "1970-01-01T00:00:00Z",
      )

    self.assertIs(status["active_ok"], True)
    self.assertIs(status["deleted_complete"], False)
    chats = (self.tmp_path / "chats.md").read_text(encoding="utf-8")
    self.assertIn("ordinary title # deleted_chat_summaries: complete", chats)
    self.assertEqual(chats.count("\n- `one`"), 1)

  def test_chat_digest_processes_active_chats_oldest_first(self):
    with mock.patch.object(
      reflection_inputs, "_api_json", side_effect=[[
        {"id": "new", "title": "new", "activity_at": "2026-08-09T10:00:00Z",
         "updated_at": "2026-08-09T10:01:00Z"},
        {"id": "old", "title": "old", "activity_at": "2026-08-08T10:00:00",
         "updated_at": "2026-08-09T11:00:00Z"},
      ], {"items": []}],
    ):
      reflection_inputs.stage_chat_digest(
        "http://example.test", "token", self.tmp_path,
        self.tmp_path / "chats.md", self.tmp_path / "chats-status.json",
        "2026-08-01T00:00:00Z",
      )
    chats = (self.tmp_path / "chats.md").read_text(encoding="utf-8")
    self.assertLess(chats.index("`old`"), chats.index("`new`"))
    self.assertIn("updated 2026-08-09T10:00:00Z", chats)

  def test_deleted_and_active_chats_share_one_recency_shape(self):
    with mock.patch.object(
      reflection_inputs, "_api_json", side_effect=[[
        {"id": "active", "title": "active", "activity_at": "2026-08-08T10:00:00Z"},
      ], {"items": [{
        "id": "deleted", "title": "deleted", "deleted_at": "2026-08-09T00:00:00Z",
        "recency_at": "2026-08-09T10:00:00Z",
      }]}],
    ):
      reflection_inputs.stage_chat_digest(
        "http://example.test", "token", self.tmp_path,
        self.tmp_path / "chats.md", self.tmp_path / "chats-status.json",
        "2026-08-01T00:00:00Z",
      )
    chats = (self.tmp_path / "chats.md").read_text(encoding="utf-8")
    self.assertLess(chats.index("`active`"), chats.index("`deleted`"))
    self.assertIn("updated 2026-08-09T10:00:00Z", chats)

  def test_chat_digest_keeps_the_whole_unreviewed_sequence(self):
    active = [
      {
        "id": f"chat-{index:02}",
        "title": f"chat {index:02}",
        "activity_at": f"2026-08-{index + 1:02}T10:00:00Z",
      }
      for index in range(25)
    ]
    with mock.patch.object(
      reflection_inputs,
      "_api_json",
      side_effect=[list(reversed(active)), {"items": [], "next_before": None}],
    ):
      status = reflection_inputs.stage_chat_digest(
        "http://example.test", "token", self.tmp_path,
        self.tmp_path / "chats.md", self.tmp_path / "chats-status.json",
        "2026-08-01T00:00:00Z",
      )

    chats = (self.tmp_path / "chats.md").read_text(encoding="utf-8")
    self.assertEqual(status["chat_count"], 25)
    self.assertEqual(status["subject_ids"], [f"chat-{index:02}" for index in range(25)])
    self.assertLess(chats.index("`chat-00`"), chats.index("`chat-24`"))

  def test_deleted_chat_backlog_paginates_until_the_checkpoint(self):
    pages = [
      [],
      {
        "items": [{
          "id": "newer", "title": "newer", "deleted_at": "2026-08-09T11:00:00Z",
          "recency_at": "2026-08-09T11:00:00Z",
        }],
        "next_before": {"recency_at": "2026-08-09T11:00:00Z", "id": "newer"},
      },
      {
        "items": [{
          "id": "older", "title": "older", "deleted_at": "2026-08-07T11:00:00Z",
          "recency_at": "2026-08-07T11:00:00Z",
        }],
        "next_before": None,
      },
    ]
    with mock.patch.object(
      reflection_inputs, "_api_json", side_effect=pages,
    ) as api:
      status = reflection_inputs.stage_chat_digest(
        "http://example.test", "token", self.tmp_path,
        self.tmp_path / "chats.md", self.tmp_path / "chats-status.json",
        "2026-08-08T00:00:00Z",
      )

    chats = (self.tmp_path / "chats.md").read_text(encoding="utf-8")
    self.assertEqual(status["chat_count"], 1)
    self.assertIn("`newer`", chats)
    self.assertNotIn("`older`", chats)
    self.assertIn("before_id=newer", api.call_args_list[2].args[2])

  def test_question_answers_stage_every_pending_packet_oldest_first(self):
    listing = {"entries": [
      {"type": "file", "name": "2026-08-09.json"},
      {"type": "file", "name": "2026-08-08.json"},
      {"type": "file", "name": "2026-08-07.json"},
    ], "next_cursor": None}
    packets = {
      "2026-08-09.json": {
        "report_date": "2026-08-09", "answered_at": "2026-08-09T12:00:00Z",
      },
      "2026-08-08.json": {
        "report_date": "2026-08-08", "answered_at": "2026-08-08T12:00:00Z",
      },
      "2026-08-07.json": {
        "report_date": "2026-08-07", "answered_at": "2026-08-07T12:00:00Z",
      },
    }

    def api(_base, _token, path):
      if "apps-list" in path:
        return listing
      return packets[path.rsplit("/", 1)[-1]]

    with mock.patch.object(reflection_inputs, "_api_json", side_effect=api):
      count = reflection_inputs.stage_question_answers(
        "http://example.test", "token", "56", "2026-08-07T18:00:00Z",
        self.tmp_path / "answers.json",
      )

    staged = json.loads((self.tmp_path / "answers.json").read_text())
    self.assertEqual(count, 2)
    self.assertEqual(
      [packet["report_date"] for packet in staged["answer_packets"]],
      ["2026-08-08", "2026-08-09"],
    )

  def test_missing_question_answer_directory_is_an_empty_backlog(self):
    missing = urllib.error.HTTPError(
      "http://example.test/question-answers/", 404, "not found", {}, None,
    )
    target = self.tmp_path / "answers.json"
    target.write_text("stale", encoding="utf-8")

    with mock.patch.object(reflection_inputs, "_api_json", side_effect=missing):
      count = reflection_inputs.stage_question_answers(
        "http://example.test", "token", "56", "2026-08-07T18:00:00Z",
        target,
      )

    self.assertEqual(count, 0)
    self.assertFalse(target.exists())

  def test_activity_status_and_snapshot_share_one_verified_contract(self):
    snapshot = self.tmp_path / "activity.jsonl"
    status = self.tmp_path / "activity-status.json"
    event = {
      "ev": "app_open",
      "ts": "2026-07-30T00:00:00+00:00",
      "app_id": 57,
    }
    content = json.dumps(event) + "\n"
    snapshot.write_text(content, encoding="utf-8")

    reflection_inputs.write_activity_status(
      status,
      ok=True,
      error="",
      event_count=reflection_inputs.validate_activity(snapshot),
      since="2026-07-29T00:00:00Z",
      sha256=hashlib.sha256(content.encode()).hexdigest(),
    )

    source, verified = reflection_inputs._activity_source(
      self.tmp_path, "2026-07-29T00:00:00Z",
    )
    self.assertIs(source["ok"], True)
    self.assertEqual(verified, content.encode())

  def test_invalid_activity_never_replaces_a_good_observation(self):
    path = self.tmp_path / "activity.jsonl"
    path.write_text('{"ev":"app_open"}\n', encoding="utf-8")

    with self.assertRaisesRegex(ValueError, "lacks string ev/ts"):
      reflection_inputs.validate_activity(path)

  def test_app_digest_separates_expected_storage_misses_from_real_errors(self):
    since = "2026-07-29T00:00:00Z"
    now = datetime.datetime(
      2026, 7, 30, tzinfo=datetime.timezone.utc,
    ).isoformat()
    events = [
      {"ev": "app_open", "ts": now, "app_id": 57},
      {
        "ev": "request_error", "ts": now, "app_id": 57,
        "method": "GET", "route": "/api/storage/apps/{app_id}/{path:path}",
        "status": 404, "count": 100,
      },
      {
        "ev": "request_error", "ts": now, "app_id": 57,
        "method": "POST", "route": "/api/apps/{app_id}/compile",
        "status": 500, "count": 2,
      },
      {
        "ev": "app_signal", "ts": now, "app_id": 57, "id": "one",
        "occurred_at": now, "name": "item_created", "payload": {},
      },
    ]
    content = "".join(json.dumps(event) + "\n" for event in events)
    (self.tmp_path / "activity.jsonl").write_text(content, encoding="utf-8")
    reflection_inputs.write_activity_status(
      self.tmp_path / "activity-status.json",
      ok=True,
      error="",
      event_count=len(events),
      since=since,
      sha256=hashlib.sha256(content.encode()).hexdigest(),
    )
    with (
      mock.patch.object(
        reflection_inputs,
        "_api_json",
        return_value=[{"id": 57, "slug": "memory", "name": "Memory"}],
      ),
      mock.patch.object(reflection_inputs, "_storage_text", return_value=None),
    ):
      digest = reflection_inputs.build_app_digest(
        "http://example.test", "token", self.tmp_path, since,
      )

    app = digest["apps"][0]
    self.assertEqual(app["slug"], "memory")
    self.assertEqual(app["name"], "Memory")
    self.assertEqual(app["opens_in_window"], 1)
    self.assertEqual(app["signal_counts"], {"item_created": 1})
    self.assertEqual(app["request_error_count_in_window"], 2)
    self.assertEqual(app["top_request_errors"][0]["status"], 500)
    self.assertEqual(app["storage_miss_count_in_window"], 100)

  def test_malformed_signal_does_not_discard_other_valid_activity(self):
    since = "2026-07-29T00:00:00Z"
    now = datetime.datetime(
      2026, 7, 30, tzinfo=datetime.timezone.utc,
    ).isoformat()
    events = [
      {"ev": "app_open", "ts": now, "app_id": 57},
      {
        "ev": "app_signal", "ts": now, "app_id": 57, "id": "bad",
        "occurred_at": now, "name": ["not", "hashable"], "payload": {},
      },
    ]
    content = "".join(json.dumps(event) + "\n" for event in events)
    (self.tmp_path / "activity.jsonl").write_text(content, encoding="utf-8")
    reflection_inputs.write_activity_status(
      self.tmp_path / "activity-status.json",
      ok=True,
      error="",
      event_count=len(events),
      since=since,
      sha256=hashlib.sha256(content.encode()).hexdigest(),
    )

    with mock.patch.object(
      reflection_inputs,
      "_api_json",
      return_value=[{"id": 57, "slug": "memory", "name": "Memory"}],
    ):
      digest = reflection_inputs.build_app_digest(
        "http://example.test", "token", self.tmp_path, since,
      )

    self.assertIs(digest["activity_source"]["ok"], True)
    self.assertEqual(digest["activity_source"]["ignored_event_count"], 1)
    self.assertEqual(digest["apps"][0]["opens_in_window"], 1)
    self.assertEqual(digest["apps"][0]["signal_counts"], {})

  def test_legacy_signal_reads_are_limited_to_apps_that_wrote_the_file(self):
    since = "2026-07-29T00:00:00Z"
    now = datetime.datetime(
      2026, 7, 30, tzinfo=datetime.timezone.utc,
    ).isoformat()
    events = [
      {
        "ev": "storage_write", "ts": now, "app_id": 2,
        "path": "signals.jsonl",
      },
      {
        "ev": "storage_write", "ts": now, "app_id": 3,
        "path": "signals.jsonl",
      },
      {
        "ev": "app_signal", "ts": now, "app_id": 3, "id": "canonical",
        "occurred_at": now, "name": "canonical_event", "payload": {},
      },
    ]
    content = "".join(json.dumps(event) + "\n" for event in events)
    (self.tmp_path / "activity.jsonl").write_text(content, encoding="utf-8")
    reflection_inputs.write_activity_status(
      self.tmp_path / "activity-status.json",
      ok=True,
      error="",
      event_count=len(events),
      since=since,
      sha256=hashlib.sha256(content.encode()).hexdigest(),
    )
    reads = []

    def storage(_base, _token, app_id, path):
      reads.append((app_id, path))
      return json.dumps({"ts": now, "name": "legacy_event"}) + "\n"

    with (
      mock.patch.object(
        reflection_inputs,
        "_api_json",
        return_value=[
          {"id": 1, "slug": "quiet"},
          {"id": 2, "slug": "legacy"},
          {"id": 3, "slug": "canonical"},
        ],
      ),
      mock.patch.object(reflection_inputs, "_storage_text", side_effect=storage),
    ):
      digest = reflection_inputs.build_app_digest(
        "http://example.test", "token", self.tmp_path, since,
      )

    self.assertEqual(reads, [("2", "signals.jsonl")])
    by_slug = {app["slug"]: app for app in digest["apps"]}
    self.assertEqual(by_slug["legacy"]["signal_counts"], {"legacy_event": 1})
    self.assertEqual(
      by_slug["canonical"]["signal_counts"], {"canonical_event": 1},
    )
    self.assertIs(by_slug["quiet"]["has_signals"], False)


if __name__ == "__main__":
  unittest.main()
