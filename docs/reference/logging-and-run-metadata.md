# Logging and run metadata

Logging is a first-class feature of this pipeline, not an afterthought. This document
is the reference for what gets recorded and why.

The argument is short: a pipeline nobody watches fails silently, and a dashboard that
quietly shows last week's numbers is worse than one that is obviously broken, because
people act on it. Every run logs, and the log is queryable.

---

## Two destinations

**Files**, under `logs/`, one per run, dated. Human-readable, useful while you are
building, gitignored.

**Tables**, in DuckDB. Queryable, joinable, and readable by Tableau - which is the
point. "When did this data last update" should be answerable from inside the dashboard,
not by opening a folder on someone's laptop.

Both come from the same `logging_setup.py` configuration. The file handler is a
standard `logging` handler; the table writer is a small shim that inserts a row.

---

## Tables

### `run_log`

One row per stage per run.

| Column | Type | Notes |
|---|---|---|
| `run_id` | VARCHAR | UUID, shared across all stages of one invocation |
| `stage` | VARCHAR | `scrape`, `transform`, `train`, `export` |
| `started_at` | TIMESTAMP | UTC |
| `finished_at` | TIMESTAMP | UTC, null while running |
| `status` | VARCHAR | `running`, `success`, `failed` |
| `rows_read` | INTEGER | Rows the stage consumed |
| `rows_inserted` | INTEGER | New rows written |
| `rows_updated` | INTEGER | Existing rows overwritten |
| `rows_unchanged` | INTEGER | Present and identical |
| `seasons` | VARCHAR | JSON array of seasons touched |
| `max_match_date` | DATE | Freshness, at a glance |
| `null_attendance_pct` | DOUBLE | Data quality, at a glance |
| `error_type` | VARCHAR | Exception class name, null on success |
| `error_message` | VARCHAR | Exception text, truncated |
| `git_sha` | VARCHAR | Which version of the code ran |
| `duration_seconds` | DOUBLE | Derived, stored for convenience |

### `check_log`

One row per check per run. Every result, pass or fail.

| Column | Type | Notes |
|---|---|---|
| `run_id` | VARCHAR | Joins to `run_log` |
| `check_name` | VARCHAR | e.g. `all_clubs_mapped` |
| `tier` | VARCHAR | `staging`, `intermediate`, `mart` |
| `passed` | BOOLEAN | |
| `metadata` | VARCHAR | JSON, check-specific detail |
| `checked_at` | TIMESTAMP | UTC |

Logging passes as well as failures is deliberate. A check that has passed for six weeks
and starts failing is a signal; a check that only writes a row when it fails gives you
no baseline to notice that against.

---

## Field notes

**`run_id` shared across stages.** Lets you ask "show me every stage of the run that
failed last Tuesday" rather than reconstructing it from timestamps.

**`rows_inserted` / `updated` / `unchanged` as three fields, not one total.** The total
is unchanged by a bug that overwrites every row with garbage. The split is the evidence
that the idempotency guard works, and it is what you point at in the
[phase 09 demo](../phases/09-break-and-fix.md#idempotency).

**`git_sha`.** Six weeks of run history is only interpretable if you know which version
of the code produced each row. A step change in row count on the same day you changed
the parser is a very different investigation from one that appeared on its own.

**`null_attendance_pct` and `max_match_date` in every row.** These are cheap, and
plotting them over time gives you a freshness and quality monitor you never had to
build. This is the phase-one equivalent of the Dagster metadata charts described in
[phase two](../phases/11-phase-two-dagster.md#attach-metadata-to-everything); when you
migrate, the same dict changes destination rather than content.

**`status = 'running'` written at stage start.** A stage that dies without writing a
terminal status leaves a `running` row, which is how you distinguish "crashed hard"
from "never started". A process killed by the OS never gets to write `failed`.

---

## What to log at each stage

| Stage | Log |
|---|---|
| `scrape` | URL per season, cache hit or miss, HTTP status, bytes, parse row count, schema-drift warnings |
| `load` | Insert/update/unchanged split, resulting table total |
| `transform` | Row count per model materialised, every check result |
| `train` | Feature count per model, train and test sizes, split date, MAE/MAPE/RMSE per model including the naive baseline, top ten features by gain |
| `export` | Files written, rows per file, destination path |

---

## Log levels

- `DEBUG` - per-request detail, cache paths, SQL being executed. Off by default.
- `INFO` - the normal narrative. One line per meaningful step. This is what you read
  when a run looks wrong.
- `WARNING` - something unexpected that did not stop the run. Extra columns from the
  source, a retry, a check that passed but near its threshold.
- `ERROR` - the run failed. Should be rare enough that its presence is meaningful.

Resist `WARNING` for things that happen every run. A log where warnings are normal is a
log where warnings are invisible.

---

## The one rule

**Never catch an exception and continue without re-raising or failing the run.** Every
silent-stale-data incident starts with a well-meant `except Exception: log.warning(...)`
that let the pipeline carry on with last week's data. If you genuinely want to continue,
record it in `run_log` in a way that makes the run visibly degraded rather than green.
