# Reflection

Möbius learns from the friction its agents actually hit.

1. **Agents log friction as it happens.** Every chat gets Reflection's
   `log_friction` tool and a short note on when to use it. Each entry records
   the exact moment it was logged, so it can be revisited later.
2. **Once a day, if new friction is waiting, Reflection works through it in
   the background.** It traces each root cause, checks whether it has been
   fixed since, and asks the logging agent why it acted as it did when the
   friction seems to come from its instructions. Then it writes a report. On
   days with no new friction, nothing runs.
3. **It asks before fixing anything.** Its questions wait beside the report.
   Only a fix you approve is made; problems already fixed since are reported
   without a question.

The app has three tabs: **Reports** (each run's report with its chat, where
you answer), **Backlog** (waiting, needs your decision, done, each linked to
the chat where it happened), and **Settings** (which agent runs it, the daily
run time, and run now).

## Pieces

| File | Role |
|---|---|
| `service.py` | The `log_friction` tool: appends to `friction.jsonl` in app storage |
| `reflection-core.md` | The note every chat gets about when to log friction |
| `fetch.sh` | Daily job: opens a Reflection chat when anything is pending |
| `friction_queue.py` | Lists entries still without an outcome; records outcomes |
| `reflection.md` | What the Reflection chat does |
| `index.jsx`, `friction.js`, `schedule.js` | The app screen |

## Install

Open the **App Store** in Möbius, find **Reflection**, and tap **Install**, or
install from URL:

```
https://raw.githubusercontent.com/mobius-os/app-reflection/main/mobius.json
```

## License

MIT — see [LICENSE](LICENSE).
