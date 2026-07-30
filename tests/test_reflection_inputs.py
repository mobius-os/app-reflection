from __future__ import annotations

import datetime
import hashlib
import json

import pytest

import reflection_inputs


def test_activity_status_and_snapshot_share_one_verified_contract(tmp_path):
  snapshot = tmp_path / "activity.jsonl"
  status = tmp_path / "activity-status.json"
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
    tmp_path, "2026-07-29T00:00:00Z",
  )
  assert source["ok"] is True
  assert verified == content.encode()


def test_invalid_activity_never_replaces_a_good_observation(tmp_path):
  path = tmp_path / "activity.jsonl"
  path.write_text('{"ev":"app_open"}\n', encoding="utf-8")

  with pytest.raises(ValueError, match="lacks string ev/ts"):
    reflection_inputs.validate_activity(path)


def test_app_digest_groups_errors_and_ignores_expected_storage_misses(
  monkeypatch, tmp_path,
):
  since = "2026-07-29T00:00:00Z"
  now = datetime.datetime(2026, 7, 30, tzinfo=datetime.timezone.utc).isoformat()
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
  (tmp_path / "activity.jsonl").write_text(content, encoding="utf-8")
  reflection_inputs.write_activity_status(
    tmp_path / "activity-status.json",
    ok=True,
    error="",
    event_count=len(events),
    since=since,
    sha256=hashlib.sha256(content.encode()).hexdigest(),
  )
  monkeypatch.setattr(
    reflection_inputs,
    "_api_json",
    lambda *_args: [{"id": 57, "slug": "memory", "name": "Memory"}],
  )
  monkeypatch.setattr(reflection_inputs, "_storage_text", lambda *_args: None)

  digest = reflection_inputs.build_app_digest(
    "http://example.test", "token", tmp_path, since,
  )

  app = digest["apps"][0]
  assert app["slug"] == "memory"
  assert app["name"] == "Memory"
  assert app["opens_24h"] == 1
  assert app["signal_counts"] == {"item_created": 1}
  assert app["request_errors_24h"] == 2
  assert app["top_request_errors"][0]["status"] == 500
