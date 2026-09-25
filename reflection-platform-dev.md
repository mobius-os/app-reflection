# Reflection add-on — Möbius platform development

Read by the Reflection nightly run only when the load condition in
`reflection.md` holds: `inputs/housekeeping.json` reports contribution records
(`source.records_read > 0`) or `needs_reasoning` items, or tonight's evidence
includes a change to Möbius's own source under `/data/platform`. It adds the
procedures for an owner who develops Möbius itself; everything else stays in
`reflection.md`, whose phases, anti-noise bar, and safety contract still apply.

Like the packaged seed, this file is owned by app updates; do not edit it
overnight. Put a lesson that generalizes into the app-owned `reflection.md`
(saying it applies when this add-on is loaded) or propose an app change in the
brief. For any platform edit, follow the platform's own development skill
(`mobius-development.md` or `platform-maintenance.md`, whichever is installed
under `/data/shared/skills/`).

## Verifying a claimed platform fix (phase 1)

A claimed backend or shell fix counts only when it is in the served clone:

- **Backend** changes belong under `/data/platform` (for example
  `/data/platform/backend/app`), not the image floor under `/app` (for example
  `/app/platform-baked/backend/app` or `/app/shell-src`). `/app` is replaced
  when the container is recreated from a new image, so an image-floor edit
  whose mtime predates the last recreate is already gone.
- **Frontend/shell** changes belong under `/data/platform/frontend`. No newer
  mtime, no `grep` hit, and no relevant `git -C /data/platform diff` means the
  edit never landed.

If it is missing, the bug stays open in the brief; never re-apply a backend or
shell behavior change overnight — that waits for a tap.

## Shipped skills and `skill/core.md` (phase 2)

A byte difference between `/data/shared/skills/<name>.md` and
`/data/platform/backend/scripts/seed-skills/<name>.md` is not itself a defect:
the active copy may hold valuable local improvements. When evidence says an
instruction is stale (an obsolete command, removed feature, or cross-skill
contradiction), compare the two, verify live behavior, and merge surgically.
Preserve valid local additions, remove dead procedure, and route a general
correction to the shipped seed plus an exact hash-gated migration or a private
contribution — fixing only one copy recreates the drift. If the conflict comes
from an always-on rule, audit `/data/platform/skill/core.md` too. Do not turn
this into an unconditional nightly diff.

## Contribution worktrees (phase 3.5)

Treat contribution worktrees as ledger-owned lifecycle state. Do not spend
agent turns recreating the deterministic inventory: the wrapper runs
`housekeeping.py` first and stages `inputs/housekeeping.json`. Read its
measured outcome and `needs_reasoning` list, trust its preserved/actionable
classifications, and investigate only the exceptions that genuinely need
judgment.

What the helper already guarantees, so you need not re-check it:

- It joins registered worktrees to Contribute's `plan.repo_path`, `status`,
  and reviewed `head_sha`. It retires a local checkout only when it is clean,
  no process has a cwd beneath it, its head stayed stable across the audit, no
  `prepared`/`draft`/`open`/`submitting` record references it, and an
  exact-head record is `merged` with a public URL.
- Patch-equivalence against upstream is computed programmatically, but an
  unreferenced equivalent worktree stays in `needs_reasoning`: topology and
  intent make that evidence, not unattended deletion authority.
- It rechecks immediately before `git worktree remove`, prunes only
  registrations whose gitdirs are already missing, removes parent directories
  only when empty, and preserves records and stored diffs.

Dirty, active, uncertain, closed-unmerged, and abandoned-unmerged work remains
for reasoning; public branches are always owner-approved actions. Before the
brief calls a change open or unapplied, check the item's `live_main` verdict
and verify the behavior on current local main (see the operating contract).
If the handoff's `status` is `unavailable` or `partial` for a reason other than
Contribute being absent, diagnose that helper boundary rather than launching a
broad replacement scan.

## Platform security and stability (phase 3.5)

When evidence points at Möbius's own source, review changed trust boundaries,
dependency alerts, missing tests or recovery coverage, and repeated failures in
the owning code path. Make only clearly behavior-preserving, reversible fixes
unattended; propose anything with user or compatibility risk. No broad nightly
audit without a fresh trigger.

## Development research and preparation (phase 5)

Add developer inputs to the general research forms: active branches and
unfinished contributions, recent errors in platform logs, missing tests around
recently changed code, and release notes for the platform's own dependencies.
Prepare the smallest next step (a focused test, a reproduction, a rebased
branch plan) rather than a broad survey.

## Brief

Developer detail (paths, SHAs, branch names, test output) belongs inside the
collapsed item; the one-line headline still says what changed for the owner.
