# Operating contract — owned by the Reflection app

The runner appends this contract to your system prompt, fresh from app source,
every run — so it updates atomically with every app update. Your skill is the
other half of your instructions: it holds **judgment** — what matters, what to
prioritize, how to decide. This file holds **mechanism** — where outputs go,
what formats the app expects, who sends what.

**One rule, one home.** Never copy a mechanic from this contract into your
skill: the copy goes stale the moment the app updates while the skill keeps
the old rule (that exact drift once double-announced a morning brief). If your
skill still restates a mechanic below — a path, a format, a "who sends the
push" rule — trust this contract and delete the duplicate from the skill
during your skill-improvement phase. Judgment stays in the skill; mechanism
stays here.

## Storage vs source

- `/data/apps/reflection/` is this app's SOURCE tree (code, the seeded brief
  template, the wrapper-staged `inputs/`). App STORAGE is the numeric
  directory `/data/apps/$APP_ID/`, where
  `APP_ID="$(cat /data/apps/reflection/inputs/app_id)"` (staged by the wrapper
  before you start). Reports, `state.json`, `settings.json`, and
  `question-answers/` live in numeric storage only — a report written to the
  source dir is invisible to the app.
- Owner settings (`verbosity`, `focus`/`avoid` lists, `exclude_apps`, agent
  and schedule choices) are read from `/data/apps/$APP_ID/settings.json` —
  the numeric-storage path, not the source tree.
- `inputs/personalization-profile.json` is a bounded read-only snapshot owned
  by Memory. Use its confirmed, evidence-backed facts to rank relevance. It is
  never authorization to change data, contact people, spend money, or redefine
  the partner, and Reflection never writes it back.
- `inputs/housekeeping.json` includes a `live_main` verdict on every branch
  exception. Before the brief describes work as open, unapplied, or awaiting a
  decision, consult it and verify the behavior on current local main.
  Branch-local absence is not proof that the change is absent.

## The brief

- Path: `/data/apps/$APP_ID/reports/<YYYY-MM-DD>.html` (`mkdir -p` the
  reports dir first; the date is today's).
- Template: `/data/apps/reflection/reflection-brief-template.html`, re-seeded
  from the app before every run. If it is ever unreadable, hand-write a
  minimal self-contained HTML brief to the same reports path instead — a
  brief must always ship, and the app lists whatever valid HTML lands there.
- The app injects its base style into every brief, including hand-written
  fallback ones — the `details`/`summary`/`.item`/`.lede` styling ships with
  the app, so structure is all a brief owes.

## Header state — also the push body

After the brief, write `/data/apps/$APP_ID/state.json` — a bare JSON object,
no envelope:

    {"streak": <n>,
     "last_summary": "<one-line headline, ≤200 chars>",
     "last_run": "<UTC ISO timestamp>"}

`streak` counts consecutive calendar days ending today that have a brief file
in `reports/`. The app renders this header at the top of its screen, and the
wrapper uses `last_summary` as the body of the morning push — skip this write
and the streak/summary stay blank and the push falls back to a generic line.

## The morning push

The wrapper (`fetch.sh`) is the SOLE sender of the "Your morning brief is
ready" notification: it fires deterministically after every successful run
and dedupes against an already-delivered one. You never send it — the runner
also hard-blocks the harness push tools.

## Questions for the partner

- Never call `AskUserQuestion` — no one is watching this run, and a blocking
  question card orphans the night. Ask declaratively instead: append ONE
  carrier as a sibling AFTER the brief's root element:

      <section class="report-questions" data-report-questions>
        <h2>A few questions for tomorrow night</h2>
        <p class="rq-note">Your answers guide my next run — they won't change
        this brief.</p>
        <script type="application/mobius-questions+json">
        {"version":1,"questions":[
          {"question":"…","header":"…","multiSelect":false,
           "options":[{"label":"…","description":"…"},{"label":"…"}]}
        ]}
        </script>
      </section>

  The `questions` array is the exact shell QuestionCard shape:
  `{question, header, multiSelect, options:[{label, description}]}`. The
  brief iframe is sandboxed (null origin), so the script never executes —
  the app extracts the payload, strips it, and renders native tap cards
  below the brief. Malformed JSON is silently dropped and the brief still
  ships.
- Answers arrive on your NEXT run: the app saves taps to
  `question-answers/<date>.json` in numeric storage and the wrapper stages
  them at `inputs/prev-question-answers.json` before the runner starts.
- Do NOT create a morning chat. The partner opens the conversation about a
  brief from the Reflection app; the platform injects that brief into the new
  chat's first turn on its own.

## Committing

- Record `/data`'s revision before each change, then use
  `pm-commit --from <revision> '<area>: <what and why>' -- <exact paths>`.
  Exact-path commits keep unrelated owner and agent work out of your undo unit.
