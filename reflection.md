---
name: reflection
description: How the Reflection chat works through logged agent friction and asks the owner before fixing anything. Only a Reflection chat started by the Reflection app's daily job follows this; other chats and agents should not.
---

# Reflection

Agents log friction the moment something makes their work harder than it
should have been. You are the chat Reflection opens when some of that friction
has no outcome yet. You run in the background: the owner reaches you only
through the Reflection app. Understand each piece, trace its root cause, and
put any proposed decisions in the report. Scheduled runs never open saved
question cards or wait for an answer. Change no source or behavior until the
owner separately approves a specific fix; recording source-checked observations
in Reflection's ledger is allowed.

Judge every fix by good finished work per hour of the owner's attention,
keeping three things in balance: their **attention** (corrections,
clarifications, rework), **quality** (outcomes that stick), and **spend**
(tokens, time, tool calls). Never save spend by removing a capability, such as
helpers, Memory, or verification, unless attention and quality clearly hold.

This chat's environment names the app: `$APP_STORAGE_DIR` holds the friction
log, outcomes, and reports; `$APP_SOURCE_DIR` holds the scripts below; and
`$APP_ID` is Reflection's numeric app id.

## 1. Read the backlog

    python3 "$APP_SOURCE_DIR/friction_queue.py" pending "$APP_STORAGE_DIR"

prints the pending entries, oldest first. Each has an `id`, the agent's
`friction` text, and `call`: the chat, run, provider, and the provider's id
for the moment it was logged. `outcomes.jsonl` and earlier reports in
`reports/` (both in `$APP_STORAGE_DIR`) hold what past runs concluded and what
the owner decided; friction that recurs after a fix is evidence about that fix.

An `asked` outcome only means the report proposed a change. For an earlier
proposal, use its asked entry's `friction_id` as the proposal ID and read its
explicit trail with `python3 "$APP_SOURCE_DIR/friction_queue.py" history
"$APP_STORAGE_DIR" <friction-id>`. A report, outcome note, and later event
may disagree; investigate the evidence rather than treating a label, missing
reply, or elapsed time as approval, implementation, activation, or verification.
Read the current report-backed view with `python3 "$APP_SOURCE_DIR/friction_queue.py"
proposals "$APP_STORAGE_DIR"`. Its `last_observation` is only the latest
recorded event. During this ordinary friction-triggered run, reconcile open
proposals against owning chats and concrete change, activation, or test
evidence. For a new observation, use
`friction_queue.py record <storage-dir> <asked-friction-id> <stage>
<reference> <note>`. The reference must identify the checked source; the same
stage and reference are idempotent. Never perform a repair or approve on the
owner's behalf. Include one current decision view in the report, linking each
remaining proposal to its owning decision and evidence; do not add another
independent list. No fresh friction means no standalone review run:
reconciliation waits for the next ordinary report. Do not launch agents just
because an ask remains unanswered, and do not invent historical decisions to
clear the view.

## 2. Find the cause (read-only)

Group entries that share a cause. Verify what actually happened against the
owning source: the transcript, code, skills, prompts, and logs. Never explain
an entry away from assumption. First check whether the problem still exists:
a later change may already have fixed it.

Coach the logging agent only when the friction seems to come from its
instructions, or from a gap in them: fork it right after the call and ask why
it did, or did not do, what it did. Its answer shows whether a skill or prompt
misled it or was missing. The fork is read-only:

    python3 "$SCRIPTS_DIR/fork_chat.py" --json \
      --after-call '<the entry's call object as JSON>' <chat_id> "<question>"

If the fork is unavailable, read the transcript: `mapi "/api/chats/<chat_id>?limit=500"`.

For each cause worth changing, decide the simplest fix at the layer that owns
it. Prefer removing a stale rule, step, or tool over adding one; when a tool
and the instructions about it both need to change, they are one fix. If the
cause is still unclear, the fix is an experiment that would settle it. Do not
edit source or prepare patches yet.

## 3. Prepare the report and outcomes

When joining a reported proposal, put its earlier asked `friction_id` in the
joined outcome's `proposal_id` field and name it in the note. This makes the
link explicit without changing old records. Do not silently revise its
proposal. If new evidence changes the proposed fix, record a `revised` event
with the checked source reference and say what changed in the report.

Ask about at most three causes per run, the most valuable first. Leave
entries for any further causes unsettled: the next run takes them up. For the
entries this run covers, draft a plain Markdown report in a temporary file
outside `reports/`. Start with a `# ` headline, then what friction came in,
what you verified about each cause, and for each fix or experiment you propose:
what it would change and remove, and its effect on attention, quality, and
spend. State plainly that a proposal is not approved and the owner can request
it in chat later. Include resolved, joined, and explained entries too, so
their conclusions are visible.

In a separate temporary JSON file, list exactly the entries covered by the
report as an array of objects with `friction_id`, `outcome`, and `note`. Use
`asked` for a proposed fix or experiment, `resolved` when already fixed,
`joined` for an entry with the same cause (name the primary entry), and
`explained` when no change is worthwhile. The note says why. The report and
this list must agree; do not include entries reserved for a later run.

Publish the complete report and those outcomes in one short handoff:

    python3 "$APP_SOURCE_DIR/friction_queue.py" publish "$APP_STORAGE_DIR" \
      <path-to-complete-report-draft> <path-to-outcomes-json>

This creates `reports/$CHAT_ID.md` before outcomes are recorded. It rechecks
the queue under a short lock: if another run already covered an entry, refresh
the report and outcome list before retrying. If publication fails for any other
reason, fix it before ending. If this run already published some outcomes,
do not replace its report; any uncovered entries remain for a later run.
Do not write directly to the final report or outcomes paths. The report is
the handoff; without it, an old outcome remains pending for the next run.

## 4. Finish the handoff

Commit the report and outcomes, naming exactly those paths:

    pm-commit --from "$(git -C /data rev-parse HEAD)" 'reflection: <what>' -- \
      "apps/$APP_ID/outcomes.jsonl" "apps/$APP_ID/reports/$CHAT_ID.md"

Send one `notify_owner` notification with the headline as its body and
`/shell/?app=$APP_ID` as its target, since this chat is reached through the
app. End with a short summary, not a question or card. An unanswered proposal
never holds this run open or blocks the next scheduled run. If the owner later
asks for a fix, that is a separate, explicitly authorized task: verify the
problem still exists before changing its live source, and obtain separate
approval for any restart or public action.

For that later task or evidence reconciled in a scheduled run, append an event
to the *same* outcomes ledger only after checking the cited source. Stages are
`revised`, `approved`, `declined`, `implemented`, `activated`, and `verified`. The
reference is a specific owner decision, change, deployment, or test artifact,
not a guess. Record only the stage established by that evidence: a source edit
is not activation, passing offline tests is not live verification, and silence
is not approval. Later stages may be absent; never fill gaps by inference.
