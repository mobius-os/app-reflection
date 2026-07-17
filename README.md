# Reflection

The nightly self-improvement loop for [Möbius](https://github.com/mobius-os). While you sleep, Möbius reflects on the day: a real agent wakes up with the whole night ahead of it and does the slow, deferred work that improves your long-term productivity. That includes learning what helps you, improving procedures and apps, and keeping storage, memory, CPU, network, and model usage economical. In the morning it hands you a one-page brief with only the decisions worth your attention.

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

The run is one multi-turn goal, not a single prompt. Working from the day's activity and chats, the agent:

1. **Interviews the chat agents worth interviewing.** It forks selected chats into throwaway copies — reusing the same provider that did the original work — and asks what was hard, what it learned, what you'd want flagged. Your real chats are never touched. Background jobs are reviewed from their logs and artifacts unless a future session registry explicitly records a safe interview target.
2. **Sharpens its own skills and the system loop.** What it learns from the interviews gets folded back into the agent's skills (including the reflection skill itself). It can read Memory's maintenance log to spot how the memory system should improve, but **Memory** owns reading, writing, and consolidating the graph.
3. **Hardens your apps.** It opens each app, exercises the paths you actually use, and fixes the small, obviously-correct breakages so you wake to working apps. Anything with a judgment call is left as a proposal, not applied.
4. **Researches what you care about and proposes features**, each tied to something it observed you doing.
5. **Stewards resources.** It reads a cheap daily disk/cgroup pulse and a bounded history, performs deeper inventories only when due or triggered by pressure/growth, safely removes proven disposable residue, and turns recurring leaks into programmatic limits. Railway deployments get stricter scrutiny because storage, RAM, CPU, and network consumption affect the bill.
6. **Writes the brief and captures feedback.** A standalone HTML brief lands in `reports/<date>.html`; any decision cards in the report are saved for the next run to act on.

When you leave feedback in the morning, that closes the loop: the agent can act on your notes and records what your answers taught it, so the next night's run wastes fewer of your taps.

## Resource stewardship

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
