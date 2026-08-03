import json
import datetime as dt
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import housekeeping


def git(repo, *args):
  result = subprocess.run(
    ["git", "-C", str(repo), *args],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
  )
  if result.returncode:
    raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
  return result.stdout.strip()


class HousekeepingTests(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.data = Path(self.tmp.name) / "data"
    self.platform = self.data / "platform"
    self.contrib = self.data / "contrib"
    self.records = self.data / "apps" / "321" / "contributions"
    self.output = self.data / "apps" / "reflection" / "inputs" / "housekeeping.json"
    self.now = dt.datetime(2026, 7, 28, 20, tzinfo=dt.timezone.utc)
    self.platform.mkdir(parents=True)
    self.contrib.mkdir()
    self.records.mkdir(parents=True)
    git(self.platform, "init", "-b", "main")
    git(self.platform, "config", "user.name", "Test Owner")
    git(self.platform, "config", "user.email", "owner@example.test")
    (self.platform / "base.txt").write_text("base\n", encoding="utf-8")
    git(self.platform, "add", "base.txt")
    git(self.platform, "commit", "-m", "base")

  def tearDown(self):
    self.tmp.cleanup()

  def add_worktree(self, name, *, commit_change=True):
    path = self.contrib / name / "worktree"
    path.parent.mkdir(parents=True)
    git(self.platform, "worktree", "add", "-b", f"fix/{name}", str(path), "main")
    if commit_change:
      (path / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
      git(path, "add", f"{name}.txt")
      git(path, "commit", "-m", name)
    return path, git(path, "rev-parse", "HEAD")

  def write_record(
    self, record_id, path, head, status="merged", updated_at=None,
    *, base=None, diff_sha256=None,
  ):
    payload = {
      "id": record_id,
      "status": status,
      "url": f"https://example.test/pr/{record_id}" if status == "merged" else None,
      "branch": f"fix/{record_id}",
      "updated_at": updated_at or "2026-07-27T18:00:00Z",
      "plan": {
        "repo_path": str(path),
        "head_sha": head,
        "base_sha": base,
        "diff_sha256": diff_sha256,
        "branch": f"fix/{record_id}",
      },
    }
    (self.records / f"{record_id}.json").write_text(
      json.dumps(payload), encoding="utf-8",
    )

  def run_helper(self, *, apply=True, active_cwds=None):
    return housekeeping.run_housekeeping(
      data_dir=self.data,
      contributions_dir=self.records,
      output=self.output,
      apply=apply,
      upstream_ref="main",
      active_cwds=active_cwds if active_cwds is not None else set(),
      now=self.now,
    )

  def test_exact_merged_head_is_removed_but_record_is_preserved(self):
    path, head = self.add_worktree("merged")
    self.write_record("merged", path, head)

    result = self.run_helper()

    self.assertEqual(result["summary"]["cleaned_count"], 1)
    self.assertEqual(result["cleaned"][0]["proof"], "exact-merged-record")
    self.assertFalse(path.exists())
    self.assertTrue((self.records / "merged.json").is_file())
    self.assertNotIn("fix/merged", git(self.platform, "branch", "--format=%(refname:short)"))
    self.assertEqual(json.loads(self.output.read_text()), result)

  def test_attribution_rewrite_is_proven_by_the_reviewed_diff(self):
    path, reviewed_head = self.add_worktree("rewritten")
    base = git(path, "rev-parse", "HEAD^")
    reviewed = subprocess.run(
      [
        "git", "-c", "core.quotePath=false", "-C", str(path),
        "diff", "--no-ext-diff", "--no-color", "--binary", "--full-index",
        "--src-prefix=a/", "--dst-prefix=b/", f"{base}..{reviewed_head}",
      ],
      check=True, capture_output=True,
    ).stdout
    digest = hashlib.sha256(reviewed).hexdigest()
    git(path, "commit", "--amend", "--no-edit", "--author", "Owner <new@example.test>")
    rewritten_head = git(path, "rev-parse", "HEAD")
    self.assertNotEqual(rewritten_head, reviewed_head)
    self.write_record(
      "rewritten", path, reviewed_head, base=base, diff_sha256=digest,
    )

    result = self.run_helper()

    self.assertEqual(result["summary"]["cleaned_count"], 0)
    self.assertTrue(path.exists())
    self.assertTrue(any(
      item["path"] == str(path.resolve())
      and "exact-reviewed-diff-proof-available" in item["reasons"]
      for item in result["needs_reasoning"]
    ))

  def test_reviewed_binary_diff_is_hashed_without_text_decoding(self):
    raw_diff = b"diff --git a/image b/image\n\xff\xfe\x00binary\n"
    record = {
      "status": "merged",
      "url": "https://example.test/pr/binary",
      "head_sha": "reviewed-head",
      "base_sha": "base",
      "diff_sha256": hashlib.sha256(raw_diff).hexdigest(),
      "updated_at": "2026-07-27T00:00:00Z",
    }
    completed = subprocess.CompletedProcess(
      ("git", "diff"), 0, raw_diff, b"",
    )

    with mock.patch.object(housekeeping, "_run_bytes", return_value=completed):
      proof, reason = housekeeping._exact_merged_proof(
        [record], "rewritten-head", self.now, self.platform,
      )

    self.assertEqual(proof["proof"], "exact-reviewed-diff")
    self.assertIsNone(reason)

  def test_unexpected_failure_replaces_success_with_unavailable_payload(self):
    self.output.parent.mkdir(parents=True, exist_ok=True)
    self.output.write_text('{"status":"ok"}\n', encoding="utf-8")
    argv = [
      "housekeeping.py",
      "--data-dir", str(self.data),
      "--contributions-dir", str(self.records),
      "--output", str(self.output),
    ]

    with mock.patch("sys.argv", argv), mock.patch.object(
      housekeeping, "run_housekeeping", side_effect=RuntimeError("boom"),
    ):
      self.assertEqual(housekeeping.main(), 0)

    payload = json.loads(self.output.read_text())
    self.assertEqual(payload["status"], "unavailable")
    self.assertIn("housekeeping-failed:RuntimeError:boom", payload["source"]["error"])

  def test_different_post_review_content_is_not_treated_as_exact(self):
    path, reviewed_head = self.add_worktree("changed-after-review")
    base = git(path, "rev-parse", "HEAD^")
    reviewed = subprocess.run(
      [
        "git", "-c", "core.quotePath=false", "-C", str(path),
        "diff", "--no-ext-diff", "--no-color", "--binary", "--full-index",
        "--src-prefix=a/", "--dst-prefix=b/", f"{base}..{reviewed_head}",
      ],
      check=True, capture_output=True,
    ).stdout
    (path / "changed-after-review.txt").write_text("later\n", encoding="utf-8")
    git(path, "add", "changed-after-review.txt")
    git(path, "commit", "-m", "later change")
    self.write_record(
      "changed-after-review", path, reviewed_head,
      base=base, diff_sha256=hashlib.sha256(reviewed).hexdigest(),
    )

    result = self.run_helper()

    self.assertEqual(result["summary"]["cleaned_count"], 0)
    self.assertTrue(path.exists())
    self.assertTrue(any(
      item["path"] == str(path.resolve())
      and "no-exact-reviewed-head" in item["reasons"]
      for item in result["needs_reasoning"]
    ))

  def test_reviewed_diff_survives_submit_time_reparenting(self):
    path, reviewed_head = self.add_worktree("reparented")
    base = git(path, "rev-parse", "HEAD^")
    reviewed = subprocess.run(
      [
        "git", "-c", "core.quotePath=false", "-C", str(path),
        "diff", "--no-ext-diff", "--no-color", "--binary", "--full-index",
        "--src-prefix=a/", "--dst-prefix=b/", f"{base}..{reviewed_head}",
      ],
      check=True, capture_output=True,
    ).stdout
    digest = hashlib.sha256(reviewed).hexdigest()
    git(self.platform, "checkout", "main")
    (self.platform / "upstream.txt").write_text("new base\n", encoding="utf-8")
    git(self.platform, "add", "upstream.txt")
    git(self.platform, "commit", "-m", "advance base")
    git(path, "rebase", "--onto", "main", base, "fix/reparented")
    self.write_record(
      "reparented", path, reviewed_head, base=base, diff_sha256=digest,
    )

    result = self.run_helper()

    self.assertEqual(result["summary"]["cleaned_count"], 0)
    self.assertTrue(path.exists())
    self.assertTrue(any(
      item["path"] == str(path.resolve())
      and "exact-reviewed-diff-proof-available" in item["reasons"]
      for item in result["needs_reasoning"]
    ))

  def test_dirty_or_actionable_work_is_never_removed(self):
    dirty_path, dirty_head = self.add_worktree("dirty")
    self.write_record("dirty", dirty_path, dirty_head)
    (dirty_path / "base.txt").write_text("uncommitted\n", encoding="utf-8")
    prepared_path, prepared_head = self.add_worktree("prepared")
    self.write_record("prepared", prepared_path, prepared_head, status="prepared")

    result = self.run_helper()

    self.assertEqual(result["summary"]["cleaned_count"], 0)
    self.assertTrue(dirty_path.exists())
    self.assertTrue(prepared_path.exists())
    self.assertTrue(any(
      item["path"] == str(dirty_path.resolve()) and "dirty" in item["reasons"]
      for item in result["needs_reasoning"]
    ))
    self.assertEqual(result["summary"]["preserved"]["actionable-checkout"], 1)

  def test_newly_merged_work_waits_a_full_cycle_before_removal(self):
    path, head = self.add_worktree("fresh")
    self.write_record(
      "fresh", path, head, updated_at="2026-07-28T08:00:00Z",
    )

    result = self.run_helper()

    self.assertTrue(path.exists())
    self.assertEqual(result["summary"]["cleaned_count"], 0)
    self.assertTrue(any(
      item["path"] == str(path.resolve())
      and "merged-quarantine" in item["reasons"]
      for item in result["needs_reasoning"]
    ))

  def test_merged_record_without_a_trustworthy_time_is_preserved(self):
    path, head = self.add_worktree("undated")
    self.write_record("undated", path, head, updated_at="not-a-time")

    result = self.run_helper()

    self.assertTrue(path.exists())
    self.assertTrue(any(
      item["path"] == str(path.resolve())
      and "merged-time-unavailable" in item["reasons"]
      for item in result["needs_reasoning"]
    ))

  def test_pathful_record_in_another_checkout_cannot_supply_merged_proof(self):
    path, head = self.add_worktree("closed")
    self.write_record("closed", path, head, status="closed")
    unrelated = {
      "id": "unrelated",
      "status": "merged",
      "url": "https://example.test/pr/unrelated",
      "branch": "fix/closed",
      "plan": {
        "repo_path": str(self.contrib / "somewhere-else" / "worktree"),
        "head_sha": head,
        "branch": "fix/closed",
      },
    }
    (self.records / "unrelated.json").write_text(
      json.dumps(unrelated), encoding="utf-8",
    )

    result = self.run_helper()

    self.assertTrue(path.exists())
    self.assertEqual(result["summary"]["cleaned_count"], 0)
    self.assertTrue(any(
      item["path"] == str(path.resolve())
      and "no-exact-reviewed-head" in item["reasons"]
      for item in result["needs_reasoning"]
    ))

  def test_unreferenced_exact_upstream_ancestor_is_reported_for_review(self):
    represented, _ = self.add_worktree("represented", commit_change=False)
    unique, _ = self.add_worktree("unique", commit_change=True)

    result = self.run_helper()

    self.assertTrue(represented.exists())
    self.assertTrue(unique.exists())
    self.assertTrue(any(
      item["path"] == str(unique) and "patches-not-upstream" in item["reasons"]
      for item in result["needs_reasoning"]
    ))
    self.assertTrue(any(
      item["path"] == str(represented)
      and "exact-upstream-ancestor-unreferenced" in item["reasons"]
      for item in result["needs_reasoning"]
    ))
    self.assertEqual(result["cleaned"], [])

  def test_dry_run_reports_without_mutating(self):
    path, head = self.add_worktree("dry")
    self.write_record("dry", path, head)

    result = self.run_helper(apply=False)

    self.assertFalse(result["applied"])
    self.assertEqual(result["summary"]["candidate_count"], 1)
    self.assertEqual(result["summary"]["cleaned_count"], 0)
    self.assertEqual(result["would_clean"][0]["proof"], "exact-merged-record")
    self.assertTrue(path.exists())

  def test_active_worktree_is_reported_for_reasoning(self):
    path, head = self.add_worktree("active")
    self.write_record("active", path, head)

    result = self.run_helper(active_cwds={path.resolve()})

    self.assertTrue(path.exists())
    self.assertTrue(any(
      item["path"] == str(path.resolve())
      and "active-process" in item["reasons"]
      for item in result["needs_reasoning"]
    ))

  def test_live_main_marks_patch_equivalent_work_as_settled(self):
    path, _ = self.add_worktree("equivalent")
    (self.platform / "equivalent.txt").write_text(
      "equivalent\n", encoding="utf-8",
    )
    git(self.platform, "add", "equivalent.txt")
    git(self.platform, "commit", "-m", "same change on main")

    result = self.run_helper(apply=False)

    item = next(
      row for row in result["needs_reasoning"] if row["path"] == str(path)
    )
    self.assertTrue(item["live_main"]["patch_equivalent"])
    self.assertFalse(item["live_main"]["actionable"])

  def test_live_main_keeps_unique_branch_work_actionable(self):
    path, _ = self.add_worktree("unique-live-main")

    result = self.run_helper(apply=False)

    item = next(
      row for row in result["needs_reasoning"] if row["path"] == str(path)
    )
    self.assertFalse(item["live_main"]["patch_equivalent"])
    self.assertTrue(item["live_main"]["actionable"])


if __name__ == "__main__":
  unittest.main()
