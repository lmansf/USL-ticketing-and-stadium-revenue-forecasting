# Scheduler entry points

The pipeline runs weekly on Tuesday morning. Matches cluster on Saturday with some
midweek fixtures, so by Tuesday the weekend is posted and settled.

Everything is behind one command - `python -m usl.run weekly` - so there is nothing
for the scheduler to orchestrate. That is deliberate: a scheduler that knows about
the pipeline's internals is a scheduler you have to keep in sync with it.

| File | Purpose |
|---|---|
| `check_attendance_coverage.py` | **Run this before you subscribe.** Real working script, serves from the archive when the response is already there |
| `propose_aliases.py` | **Run this after the first backfill.** Derives the provider-id rows of `club_aliases.csv` from the archive, through the name rows already there |
| `run_weekly.ps1` | Weekly run, Windows Task Scheduler |
| `run_weekly.sh` | Weekly run, cron / launchd / systemd timer |

## The attendance gate

```
python scripts/check_attendance_coverage.py                    # free, example key
python scripts/check_attendance_coverage.py --season-id 1234   # a real USL season
```

Attendance is the target variable and there is no second source, so this is the check
the whole project rests on. It reports the share of matches carrying a usable figure
and the median, because the dangerous outcome is a field that exists but is mostly
zeroes - a yes/no check calls that a pass. Exits non-zero below 80 percent populated.

The first command passes today, offline, against the archived example season: 380 of
380 matches populated, median 31,957. That proves the schema, not USL coverage. The
second command is the one that decides the project and it needs the subscription.

See [phase 00](../docs/phases/00-data-access-and-the-clock.md#the-gate-verify-attendance-before-you-pay).

## The alias proposal

```
python scripts/propose_aliases.py            # dry run: prints the rows it would add
python scripts/propose_aliases.py --write    # appends them to usl/ref/club_aliases.csv
```

The staging join is on the provider's numeric club id, and those ids exist only
in the data. The CSV carries a name row per USL club in advance; this script reads
every archived `league-matches` response, pairs each id with the name the API gave
it, and matches the name to a name row loosely - case, punctuation, and a trailing
FC or SC do not matter, because a person reads the output before it is written and
the join itself stays exact. Ids it cannot match, or that match two clubs, are
listed for mapping by hand; an id already in the CSV whose name points at a
different club is a conflict and is never written. Exit code 1 while anything is
unmatched, so the loop is: run, resolve the list, run again. Archive only: no key,
nothing spent.

On the committed example season it reports twenty ids, all already mapped.

## Exit codes

The weekly scripts propagate the pipeline's exit code so the scheduler's result means
something:

| Code | Meaning |
|---|---|
| 0 | Every stage succeeded and every check passed |
| 1 | A stage failed or a data-quality check failed. The run log row names it |
| 3 | The database was locked by another process for the whole retry window. Only the file log has it - the run log lives in the locked database |

## Scheduling

Registration walkthrough: [docs/mvp/05-mvp-schedule.md](../docs/mvp/05-mvp-schedule.md)

Phase two replaces this with Dagster, which brings run history and asset lineage.
The scheduled task does the job; what it does not give you is anything to browse.
See [docs/phases/11-phase-two-dagster.md](../docs/phases/11-phase-two-dagster.md).
