# MVP 05 - Schedule it for Tuesday

**Goal.** The whole pipeline runs unattended, weekly, on Tuesday morning, and you can
tell afterwards whether it worked.

**Extends into:** [phase 02](../phases/02-duckdb-and-the-lock-problem.md),
[phase two: Dagster](../phases/11-phase-two-dagster.md)

---

## Why Tuesday

Matches cluster on Saturday with some midweek fixtures. By Tuesday the weekend is
posted and settled - attendance figures are up, and the corrections that follow a match
have mostly landed. Monday is too early and catches half-published data; later in the
week means the dashboard is stale for longer than it needs to be.

---

## One command

Everything is behind `python -m usl.run weekly`, which runs scrape, transform, train,
and export in order and writes one run-log row per stage. There is nothing for the
scheduler to orchestrate - that is deliberate, because a scheduler that knows about
your pipeline's internals is a scheduler you have to keep in sync with it.

---

## Windows Task Scheduler

`scripts/run_weekly.ps1` is the entry point. It activates the virtual environment,
runs the CLI, and writes stdout and stderr to a dated log file under `logs/`.

Register it:

1. Open Task Scheduler, **Create Task** (not Create Basic Task - you need the extra
   settings tabs).
2. **General:** name it, and select **Run whether user is logged on or not**.
3. **Triggers:** New, Weekly, Tuesday, 06:00. Pick a time when the machine is on but
   you are not using it.
4. **Actions:** New, Start a program.
   - Program: `powershell.exe`
   - Arguments: `-NoProfile -ExecutionPolicy Bypass -File "C:\path\to\repo\scripts\run_weekly.ps1"`
   - Start in: `C:\path\to\repo`
5. **Conditions:** clear **Start the task only if the computer is on AC power** if this
   is a laptop, or it will silently skip weeks you were on battery.
6. **Settings:** enable **Run task as soon as possible after a scheduled start is
   missed**.

The **Start in** field is the one people miss. Without it the working directory is
`C:\Windows\System32`, relative paths in `config.py` resolve somewhere unexpected, and
the failure is confusing out of proportion to its cause. Use absolute paths in config
as a belt-and-braces measure.

> **On macOS or Linux**, the equivalent is a crontab entry -
> `0 6 * * 2 cd /path/to/repo && ./scripts/run_weekly.sh` - or a launchd plist or
> systemd timer. `scripts/run_weekly.sh` is provided. The scheduler is the only
> platform-specific piece; everything else in this repo runs anywhere.

---

## Verifying it ran

A scheduled task that fails silently is worse than no scheduled task, because you
believe the dashboard.

Three things to check, in increasing order of trustworthiness:

- **Task Scheduler's Last Run Result.** `0x0` means the process exited zero. It does
  not mean the pipeline did anything useful - a run that scraped nothing and wrote
  nothing also exits zero.
- **The log file** under `logs/`. Dated, one per run.
- **The run log table** in DuckDB. This is the one that matters, because Tableau can
  read it - so "when did this data last update" is answerable from inside the
  dashboard rather than by opening a folder.

---

## Exercise M5.1 - The silent Tuesday

Your Tuesday run exits zero, the log file exists, and the dashboard shows last week's
numbers. What happened, and how would you have known without checking manually?

<details>
<summary>Solution</summary>

Most likely one of three things, and they are hard to tell apart from the exit code
alone:

- The source had not posted the weekend's matches yet, so the scrape landed zero new
  rows. Correct behaviour, stale outcome.
- The scrape failed, an exception was caught somewhere and logged as a warning, and the
  pipeline carried on with the data it already had.
- The write failed on a DuckDB lock because Tableau was open, and the same thing
  happened.

The check is on *recency*, not on success:

```python
latest = con.sql("SELECT max(date) FROM stg_matches").fetchone()[0]
age_days = (date.today() - latest).days
in_season = SEASON_START <= date.today() <= SEASON_END
passed = (age_days <= 10) or not in_season
```

Record the result in the run log and exit non-zero when it fails, so Task Scheduler's
Last Run Result becomes meaningful rather than decorative.

The `in_season` guard is what makes this a check you keep. An eighty-day gap in January
is correct, and a check that fires every week in the off-season gets ignored by
February and then it is not a check at all.

The second and third causes above share a root: an exception that got caught and
downgraded. Be suspicious of every `except` in your pipeline that does not re-raise.
</details>

---

## The lock, again

The MVP answer in [MVP 01](01-mvp-ingest-to-duckdb.md#the-lock) was "close Tableau
first". Scheduling is where that stops working, because at 06:00 on Tuesday nobody is
there to close anything.

This is the point where you need the guard from
[phase 02](../phases/02-duckdb-and-the-lock-problem.md#guard-two---handle-the-lock) -
the one exercise in this guide with no worked solution. Retry, or write to a temp file
and swap it in, or both. Whatever you choose, the run log has to make it obvious which
happened.

---

## Done when

- The task fires on Tuesday and completes without you present.
- A run that lands no new matches during the season is visible as such rather than
  reported green.
- The run log table has a row per stage per run, readable from Tableau.

---

That is the MVP. Everything works end to end. Now go to the
[full track](../phases/) and make it the version you show someone - the
[graduation order](README.md#graduating) is at the bottom of the MVP index.
