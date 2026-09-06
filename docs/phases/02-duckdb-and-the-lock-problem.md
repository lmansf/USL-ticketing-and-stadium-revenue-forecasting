# Phase 02 - DuckDB and the lock problem

**Goal.** A single DuckDB file that the weekly job can always write to, and a run log
that makes it obvious when it could not.

**MVP cut.** The MVP track uses the same database file and the same run log. It does
not solve the lock problem - it just closes Tableau before running. That is a real
answer for a laptop, and a bad one for anything scheduled.

**Modules.** `usl/db.py`, `usl/logging_setup.py`

---

## The problem

DuckDB is single-writer. If Tableau Desktop holds the database file open when
Tuesday's job fires, the write fails.

The failure mode that matters is not the crash. It is the *silence*. The job fails at
06:00, nobody is watching, and the dashboard sits there quietly showing last week's
numbers all week. Everyone downstream believes it. That is demo scenario D1 in
[phase 09](09-break-and-fix.md), and it is the failure this phase exists to prevent.

Two guards. Build the first one now, because it is what makes the second one
debuggable.

---

## Guard one - log every run and its outcome

Logging is a first-class feature of this pipeline, not an afterthought bolted on at
the end. Every ingest run logs. The run log is a table in the database, not just a
text file, so Tableau can read it and so a stale run is visible in the dashboard
rather than only in a console nobody opens.

Capture as much run metadata as you reasonably can. The full list this repo expects,
and the reasoning behind each field, is in
[reference/logging-and-run-metadata.md](../reference/logging-and-run-metadata.md).
At minimum: run id, stage, start and end time, status, rows read, rows inserted, rows
updated, rows unchanged, seasons touched, max match date, null-attendance rate, and
the exception text when there is one.

Six weeks of that and you have a freshness monitor you never had to build - row counts
and null rates per run, straight out of a table.

### Exercise 2.1 - The stale-run trap

The Tuesday run "succeeds" but the site has not posted the weekend's matches yet, so
nothing new lands. Status is green and the data is stale. How do you catch it?

Note that the naive version of this check fires every week in January, and a check
that cries wolf in the off-season is a check people mute.

<details>
<summary>Solution</summary>

Check recency, not just success, and make the threshold conditional on where you are
in the calendar:

```python
def check_matches_are_fresh(con) -> CheckResult:
    latest = con.sql("SELECT max(date) FROM stg_matches").fetchone()[0]
    age_days = (date.today() - latest).days
    in_season = SEASON_START <= date.today() <= SEASON_END
    return CheckResult(
        name="matches_are_fresh",
        passed=(age_days <= 10) or not in_season,
        metadata={"latest_match": str(latest), "age_days": age_days},
    )
```

The `in_season` guard is the whole point. An eighty-day gap in January is correct, not
a failure. Encoding "what does normal look like right now" is the difference between a
check people trust and one they turn off.

In phase one this returns a result that the runner records and, on failure, exits
non-zero on. In [phase two](11-phase-two-dagster.md) the same function becomes a
Dagster asset check with no change to its body - which is the argument for writing
checks as plain functions now.
</details>

---

## Guard two - handle the lock

This one you work out yourself. There is no solution block for it.

**What you have to satisfy:**

- Tuesday's scheduled run completes even when Tableau has been left open on the
  database file, or fails in a way that names the cause in one line.
- A reader that opens the file mid-run sees either the complete previous state or the
  complete new state. Never a half-written one.
- The failure message, read six months from now by someone who has forgotten this
  code exists, is enough to act on.

**What you have to decide:**

- Retry, or write to a temporary database and swap the file in once it is complete, or
  both. They fail differently and cost differently.
- If you retry: how many attempts, how long between them, and whether the delay is
  constant or grows.
- Which exceptions mean "locked" and which mean something genuinely wrong that must
  not be retried. Retrying the wrong error class turns a real bug into a slow one.
- If you swap: what happens if the process dies between writing the temp file and
  replacing the original, and whether the replace operation you chose is atomic on the
  platform this actually runs on.
- What the run log records in each case, so that D1 in the demo has a log line to
  point at.

**Where it goes.** `usl/db.py`, in `connect_for_write` and `commit_and_swap`. Both are
stubs with the contract in the docstring and no implementation.

**How you know it works.** Open the DuckDB file in a second process, hold it, and run
the job. Then do it again while killing the job partway through, and check that the
database is still readable and internally consistent afterwards.

This is the one exercise in the guide left completely unguided, because it is the one
where the reasoning matters more than the code, and because you asked to hit it
yourself first.

> **Resolved in this build.** The route taken is retry with backoff, lock errors only,
> then a failure naming the holding process and PID; `commit_and_swap` was deleted. The
> reasoning - why a swap does not fix the failure it is meant to fix, and what the run
> log can and cannot record when the database itself is the thing locked - is in
> [reference/build-decisions.md](../reference/build-decisions.md#phase-02---the-lock),
> and `tests/test_db_lock.py` covers the five scenarios above.

---

## What "done" looks like

- Every invocation of `python -m usl.run` writes a row to the run log, success or
  failure.
- A run that lands zero new matches during the season is visible as such.
- The database survives a write attempt made while another process holds the file
  open, and the outcome is legible in the log either way.
- `tests/test_run_log.py` passes.

Next: [phase 03 - Club name consistency](03-club-name-consistency.md).
