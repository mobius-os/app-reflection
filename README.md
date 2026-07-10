# Reflection

The nightly self-improvement loop for [Möbius](https://github.com/mobius-os). While you sleep, Möbius reflects on the day: a real agent wakes up with the whole night ahead of it and does the slow, deferred system-improvement work the daytime agent never has time for. In the morning it hands you a one-page brief and a short conversation with a few decisions to tap through over coffee.

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

1. **Interviews the agents that worked during the day.** It forks each chat (and each app sub-agent run) into a throwaway copy — reusing the same provider that did the original work — and asks what was hard, what it learned, what you'd want flagged. Your real chats are never touched.
2. **Sharpens its own skills and the system loop.** What it learns from the interviews gets folded back into the agent's skills (including the reflection skill itself). It can read Memory's maintenance log to spot how the memory system should improve, but **Memory** owns reading, writing, and consolidating the graph.
3. **Hardens your apps.** It opens each app, exercises the paths you actually use, and fixes the small, obviously-correct breakages so you wake to working apps. Anything with a judgment call is left as a proposal, not applied.
4. **Researches what you care about and proposes features**, each tied to something it observed you doing.
5. **Writes the brief and captures feedback.** A standalone HTML brief lands in `reports/<date>.html`; any decision cards in the report are saved for the next run to act on.

When you leave feedback in the morning, that closes the loop: the agent can act on your notes and records what your answers taught it, so the next night's run wastes fewer of your taps.

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
