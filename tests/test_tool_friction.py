import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import tool_friction


def _db(path):
  con = sqlite3.connect(path)
  con.executescript("""
    create table chats (id text primary key, title text, messages text);
    create table chat_runs (
      chat_id text, status text, cost_usd real, started_at text,
      input_tokens integer, output_tokens integer,
      cache_read_input_tokens integer, total_tokens integer
    );
  """)
  return con


class ToolFrictionTests(unittest.TestCase):
  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.tmp_path = Path(self.temp_dir.name)

  def tearDown(self):
    self.temp_dir.cleanup()

  def test_command_family_does_not_expose_an_unknown_script_name(self):
    family = tool_friction._command_family(
      "Bash", "/private/deploy-acme-prod.sh --force",
    )

    self.assertEqual(family, "Bash")
    self.assertNotIn("acme", family)

  def test_recent_tool_friction_is_bounded_and_grouped(self):
    db = self.tmp_path / "test.db"
    con = _db(db)
    now = dt.datetime(2026, 7, 28, 12, tzinfo=dt.timezone.utc)
    messages = [{
    "role": "assistant",
    "ts": int((now - dt.timedelta(hours=1)).timestamp() * 1000),
    "blocks": [
      {
        "type": "tool", "tool": "Bash",
        "input": "bash $SCRIPTS_DIR/agent-screenshot.sh /app/57",
        "output_exit_code": 1, "output_truncated": False,
        "output_full_len": 120,
      },
      {
        "type": "tool", "tool": "Bash",
        "input": "rg -n capture frontend/src", "output_exit_code": 0,
        "output_truncated": True, "output_full_len": 5_000,
      },
      {
        "type": "tool", "tool": "Bash",
        "input": "rg -n capture frontend/src", "output_exit_code": 0,
        "output_truncated": True, "output_full_len": 5_000,
      },
    ],
    }]
    con.execute(
      "insert into chats values (?,?,?)",
      ("chat-1", "Visual work", json.dumps(messages)),
    )
    con.execute(
      "insert into chat_runs values (?,?,?,?,?,?,?,?)",
      ("chat-1", "completed", 1.25, (now - dt.timedelta(hours=1)).isoformat(),
       100, 20, 80, 120),
    )
    con.commit()
    con.close()

    result = tool_friction.analyse_database(str(db), hours=24, now=now)

    self.assertEqual(result["overall"]["tool_calls"], 3)
    self.assertEqual(result["overall"]["failed_calls"], 1)
    self.assertEqual(result["overall"]["truncated_calls"], 2)
    self.assertEqual(result["overall"]["output_bytes"], 10_120)
    self.assertEqual(result["overall"]["failure_rate"], 0.3333)
    self.assertEqual(result["overall"]["truncation_rate"], 0.6667)
    self.assertEqual(result["primitives"]["visual_capture"]["failed_calls"], 1)
    self.assertEqual(result["primitives"]["source_inspection"]["truncated_calls"], 2)
    self.assertEqual(result["run_totals"]["cost_usd"], 1.25)
    self.assertEqual(result["run_totals"]["cost_per_completed_run"], 1.25)
    self.assertEqual(result["run_totals"]["cache_read_share"], 0.8)
    self.assertEqual(result["failure_classes"], {"nonzero_exit": 1})
    self.assertEqual(result["failure_families"], {"authenticated screenshot": 1})
    self.assertNotIn("sample", result["repeated_calls"][0])
    self.assertEqual(result["repeated_calls"][0]["family"], "source search")
    source_family = next(
      item for item in result["command_families"]
      if item["family"] == "source search"
    )
    self.assertEqual(source_family["count"], 2)
    self.assertEqual(source_family["chat_count"], 1)
    self.assertEqual(result["daily"], [{
      "date": "2026-07-28",
      "tool_calls": 3,
      "failed_calls": 1,
      "truncated_calls": 2,
      "completed_runs": 1,
      "cost_usd": 1.25,
      "total_tokens": 120,
      "input_tokens": 100,
      "cache_read_input_tokens": 80,
      "failure_rate": 0.3333,
      "truncation_rate": 0.6667,
      "cache_read_share": 0.8,
    }])
    self.assertEqual(result["repeated_calls"][0]["count"], 2)

  def test_old_messages_do_not_leak_into_window(self):
    db = self.tmp_path / "test.db"
    con = _db(db)
    now = dt.datetime(2026, 7, 28, 12, tzinfo=dt.timezone.utc)
    messages = [{
    "role": "assistant",
    "ts": int((now - dt.timedelta(days=2)).timestamp() * 1000),
    "blocks": [{"type": "tool", "tool": "Bash", "input": "pytest"}],
    }]
    con.execute(
      "insert into chats values (?,?,?)", ("chat-1", "Old", json.dumps(messages)),
    )
    con.execute(
      "insert into chat_runs values (?,?,?,?,?,?,?,?)",
      ("chat-1", "completed", 2, (now - dt.timedelta(hours=1)).isoformat(),
       1, 1, 0, 2),
    )
    con.commit()
    con.close()

    result = tool_friction.analyse_database(str(db), hours=24, now=now)
    self.assertEqual(result["overall"]["tool_calls"], 0)
    self.assertEqual(result["run_totals"]["completed_runs"], 1)


if __name__ == "__main__":
  unittest.main()
