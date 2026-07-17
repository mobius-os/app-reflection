# Reflection

The nightly self-improvement loop for [Möbius](https://github.com/mobius-os). While you sleep, a real agent steps back from individual tasks and looks at the larger picture: how you work, what the system learned, what caused friction, what may matter tomorrow or next week, and how Möbius itself should evolve. In the morning it leaves a concise brief with the useful outcomes and only the decisions worth your attention.

It always ships a brief, even on quiet nights — Möbius's reflection skill mandates a brief every run.

## Install

### Via the App Store (recommended)

Open the **App Store** mini-app in Möbius, find **Reflection**, tap **Install**.

### Via paste-a-URL

In the App Store, choose **Install from URL** and paste:

```
https://raw.githubusercontent.com/mobius-os/app-reflection/main/mobius.json
```

Möbius fetches the manifest, shows you the requested permissions and schedule, and installs with one tap.

## What a run does

The run is one multi-turn goal, not a fixed checklist. It works from recent activity, chats, logs, code, prior Reflection learning, and current web research, then chooses the few highest-leverage moves. A night may:

1. **Review yesterday's work.** Find repeated effort, weak procedures, unfinished loops, avoidable failures, and things a future agent should know.
2. **Evolve its own approach.** Rewrite its compact operating model, append durable learning, prune stale prompt rules, and improve the Reflection skill when evidence shows a better way to work.
3. **Anticipate what is likely to help.** Prepare context, research, fixes, or small improvements for the next day and week. This can include checking relevant releases or practices for tools and dependencies you actually use—not generic news gathering.
4. **Improve the system.** Harden an app or workflow, simplify a recurring process, improve observability, reduce unnecessary usage, or turn repeated cleanup into a bounded lifecycle rule.
5. **Learn about you carefully.** Update its model only from observed patterns and feedback, keeping hypotheses distinct from confirmed preferences.
6. **Write the brief.** A standalone HTML brief lands in `reports/<date>.html`; optional decision cards are saved for the next run, but unanswered questions never block useful work.

When you leave feedback in the morning, that closes the loop: the agent can act on your notes and records what your answers taught it, so the next night's run wastes fewer of your taps.

## How Reflection evolves

`meta-state.md` is Reflection's concise current operating model: observed working
patterns, system strengths and friction, near-term hypotheses, a small watchlist,
and the cadence for revisiting each item. Reflection rewrites it when evidence
changes the model. `meta-learning.jsonl` is the bounded explanation of why the
model or prompt changed, so a later run does not have to rediscover the lesson.

The editable Reflection skill is its procedure, not a diary. Reflection changes
that prompt only when a finding generalizes to future runs, and removes rules
that have become stale or redundant. This lets the approach evolve without the
prompt growing forever.

Web research is driven by the user's real work and a near-term horizon. For
example, Reflection may check whether a frequently used tool has a relevant new
release, whether a dependency changed in a way that affects an active project,
or whether tomorrow's likely task can be prepared in advance. Each watch records
when it was checked and when it is worth checking again.

## Bounded resource evidence

`resource_monitor.py` records a small daily snapshot in `resource-history.jsonl`.
It always reads cheap filesystem and cgroup counters, but walks `/data` only on
the first run, on its weekly cadence, when disk pressure rises, or when daily
growth is unusual. Deep scans have a wall-clock budget and histories are
bounded, so the observer cannot quietly become the resource leak.

Reflection records each cleanup or policy decision in
`resource-decisions.jsonl`: the evidence, action, measured result, next review
date, and trigger that warrants an earlier look. Once an area has been hardened
and its analytics remain healthy, Reflection lengthens its review cadence
instead of rerunning the same diagnostics. It automatically cleans only data
that is demonstrably regenerable, expired, inactive, and narrowly targeted;
user content, credentials, databases, and uncertain backups remain proposals.
The wrapper also retains 60 compact run-metric rows (duration, exit status,
disk delta, cgroup CPU delta, and whether the brief shipped), alongside only a
short log tail, so Reflection can reduce its own footprint without creating an
ever-growing observability store.

## No sandbox, by design

The agent runs with **full tools and a real token** — it is the agent, trusted, not a locked-down script. That is deliberate: Möbius's philosophy is *code empowers the agent; it does not police it*. Safety comes from instruction, not tool-denial — every change it makes is in `/data`'s git history and reversible, it never auto-applies anything risky (security changes with behavior impact, destructive data ops, dependency major-bumps, anything that hits paid APIs or notifies other people), and it surfaces those as proposals in the brief instead.

## Customize

From the app's **Settings** tab:

- **Agent / Model** — Claude or Codex; any model from a connected provider.
- **Run time** — when the cron fires. Defaults to 06:00 local (DST handled); untick "use my local time" to pin to UTC.

Schedule changes take effect within 10 minutes (the cron sync runs every 10). What it prioritizes, how it interviews, and how long the brief runs live in the editable reflection skill, which the agent revises itself as it learns what's worth doing.

## Streak

The streak counts consecutive days a brief was produced. The reflection skill mandates a brief every run, so the streak grows each night the run completes and is never reset by a "quiet" night — only by a run that actually failed or was skipped (lock-held or config error).

## License

MIT — see [LICENSE](LICENSE).
