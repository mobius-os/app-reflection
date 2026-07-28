import datetime as dt
import json
import sqlite3

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


def test_recent_tool_friction_is_bounded_and_grouped(tmp_path):
  db = tmp_path / "test.db"
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
    "insert into chats values (?,?,?)", ("chat-1", "Visual work", json.dumps(messages)),
  )
  con.execute(
    "insert into chat_runs values (?,?,?,?,?,?,?,?)",
    ("chat-1", "completed", 1.25, (now - dt.timedelta(hours=1)).isoformat(),
     100, 20, 80, 120),
  )
  con.commit()
  con.close()

  result = tool_friction.analyse_database(str(db), hours=24, now=now)

  assert result["overall"] == {
    "tool_calls": 3,
    "failed_calls": 1,
    "truncated_calls": 2,
    "output_bytes": 10_120,
    "chat_count": 1,
    "assistant_turns": 1,
  }
  assert result["primitives"]["visual_capture"]["failed_calls"] == 1
  assert result["primitives"]["source_inspection"]["truncated_calls"] == 2
  assert result["run_totals"]["cost_usd"] == 1.25
  assert result["repeated_calls"][0]["count"] == 2


def test_old_messages_do_not_leak_into_window(tmp_path):
  db = tmp_path / "test.db"
  con = _db(db)
  now = dt.datetime(2026, 7, 28, 12, tzinfo=dt.timezone.utc)
  messages = [{
    "role": "assistant",
    "ts": int((now - dt.timedelta(days=2)).timestamp() * 1000),
    "blocks": [{"type": "tool", "tool": "Bash", "input": "pytest"}],
  }]
  con.execute("insert into chats values (?,?,?)", ("chat-1", "Old", json.dumps(messages)))
  con.execute(
    "insert into chat_runs values (?,?,?,?,?,?,?,?)",
    ("chat-1", "completed", 2, (now - dt.timedelta(hours=1)).isoformat(), 1, 1, 0, 2),
  )
  con.commit()
  con.close()

  result = tool_friction.analyse_database(str(db), hours=24, now=now)
  assert result["overall"]["tool_calls"] == 0
  assert result["run_totals"]["completed_runs"] == 1
