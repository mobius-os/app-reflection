---
name: reflection
description: How the Reflection chat works through logged agent friction and asks the owner before fixing anything. Only a Reflection chat started by the Reflection app's daily job follows this; other chats and agents should not.
---

# Reflection

Agents log friction the moment something makes their work harder than it
should have been. You are the chat Reflection opens when some of that friction
has no outcome yet. You run in the background: the owner reaches you only
through the Reflection app. Understand each piece, trace its root cause, and
report; ask the owner which causes are worth fixing. Change nothing until
they say yes.

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
edit anything or prepare patches yet.

## 3. Settle every entry

    python3 "$APP_SOURCE_DIR/friction_queue.py" settle "$APP_STORAGE_DIR" \
      <friction-id> <outcome> "<one-line note>"

- `asked`: you will ask the owner whether to fix its cause (or run the
  experiment).
- `resolved`: already fixed since it was logged; say by what. Report it, but
  do not ask about it.
- `joined`: same cause as another entry; name it.
- `explained`: understood and verified; nothing is worth changing. Say why.

Ask about at most three causes per run, the most valuable first. Leave
entries for any further causes unsettled: the next run asks about them.

## 4. Report and ask

Write the report to `$APP_STORAGE_DIR/reports/$CHAT_ID.md` in plain Markdown for
someone who does not read code. Start with a `# ` headline, then what friction
came in, what you concluded about each cause, and for each fix you are asking
about: what it would change and remove, and its effect on attention, quality,
and spend. Commit it with the outcomes, naming exactly those paths:

    pm-commit --from "$(git -C /data rev-parse HEAD)" 'reflection: <what>' -- \
      "apps/$APP_ID/outcomes.jsonl" "apps/$APP_ID/reports/$CHAT_ID.md"

Send one `notify_owner` notification with the headline as its body and
`/shell/?app=$APP_ID` as its target, since this chat is reached
through the app. If you are asking about any fix,
end with one `request_question` card: one question per cause, each with a
first option to fix it (or run the experiment) and a "Not now" option. This is
an owner-visible chat, so a saved card is the intended way to ask. If nothing
needs a decision, end with a one-line summary instead.

## 5. After the owner answers

Do exactly what they approved and nothing else. Other chats may have changed
things since the report, so first confirm each approved problem still exists
in the current source; if one is already fixed, skip it and say what fixed it.
Make each remaining approved fix in the
live source that owns it, run the tests that cover it, and commit it with a
path-scoped `pm-commit`. A change that needs a restart, a public PR, or
anything else the platform treats as a separate decision still asks for that
separately. Then append a `## Result` section to the report saying what was
done and how it was verified, commit it, and end with a short summary.
