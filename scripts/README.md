# Scheduler entry points

The pipeline runs weekly on Tuesday morning. Matches cluster on Saturday with some
midweek fixtures, so by Tuesday the weekend is posted and settled.

Everything is behind one command - `python -m usl.run weekly` - so there is nothing
for the scheduler to orchestrate. That is deliberate: a scheduler that knows about
the pipeline's internals is a scheduler you have to keep in sync with it.

| File | Platform |
|---|---|
| `run_weekly.ps1` | Windows, for Task Scheduler |
| `run_weekly.sh` | macOS and Linux, for cron, launchd, or a systemd timer |

Registration walkthrough: [docs/mvp/05-mvp-schedule.md](../docs/mvp/05-mvp-schedule.md)

Phase two replaces this with Dagster, which brings run history and asset lineage.
The scheduled task does the job; what it does not give you is anything to browse.
See [docs/phases/11-phase-two-dagster.md](../docs/phases/11-phase-two-dagster.md).
