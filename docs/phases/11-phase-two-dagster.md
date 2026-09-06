# Phase two - Dagster orchestration

> **Status: built.** The asset graph lives in `usl/defs.py` and `usl/assets/`,
> wraps the phase-one functions unchanged, and runs in-process with the same
> checks between tiers. `make dagster` opens the UI with the weekly schedule
> defined; `tests/test_dagster.py` materialises the whole graph on the example
> season. The phase-one scheduled task still works and is still the default -
> see [How it landed](#how-it-landed) at the end for what changed and what did
> not. The guide text below is kept as written.

---

## Why bother, given the scheduled task works

Task Scheduler runs the job. It does the thing. What it does not give you is run
history, asset lineage, and failure visibility - and if you are applying for a BI role,
those are the things you would actually be asked about.

The concrete gap: after eight weeks of scheduled runs you have eight rows in your run
log and a folder of log files. After eight weeks of Dagster you have a browsable
timeline of every materialization, a lineage graph showing which table came from which,
and automatically plotted charts of every numeric metadata field you attached. The
second one is a conversation. The first one is a table you would have to build a view
over.

That is the whole argument, and it is worth being honest that it is a presentation
argument rather than a correctness one. The pipeline is not more correct with Dagster.
It is more legible.

---

## The asset graph

```
raw_matches ──┐
              ├──> stg_matches ──> int_standings ──┐
raw_weather ──┘                                    ├──> mart_match_features ──┐
                                                   │                          ├──> predictions
club_aliases ─────────────────────────────────────┘                          └──> model_metrics
```

Schedule the whole graph weekly, Tuesday morning. Same day, same reasoning as phase
one - matches cluster on Saturday with some midweek fixtures, and Tuesday means the
weekend is posted and settled.

`raw_weather` is [phase two as well](12-phase-two-weather.md); the graph shows both
deferred pieces because they land together.

---

## What migrates cleanly, and why

Phase one is deliberately written so that the migration is mostly decoration rather
than a rewrite:

| Phase one | Becomes | Change required |
|---|---|---|
| A function in `usl/transform/runner.py` that materialises one table | `@asset` | Decorator plus a return |
| A `CheckResult` from `usl/transform/checks.py` | `@asset_check` | Wrap the existing function body |
| A row written to the run log | Materialization metadata | Pass the same dict to `Output(metadata=...)` |
| `python -m usl.run weekly` in Task Scheduler | `ScheduleDefinition` | Delete the scheduled task |

This is why the checks in phase one are plain functions returning a result object
rather than assertions scattered through the transform code. An assertion cannot become
an asset check without being rewritten; a function returning `CheckResult` can.

---

## Attach metadata to everything

This is the part you will learn most from later. Every materialization should carry
metadata:

```python
return Output(
    value=df,
    metadata={
        "rows": len(df),
        "rows_inserted": stats.inserted,
        "rows_updated": stats.updated,
        "seasons": sorted(df["season"].unique().tolist()),
        "max_match_date": str(df["date"].max()),
        "null_attendance_pct": round(df["attendance"].isna().mean() * 100, 2),
        "preview": MetadataValue.md(df.head().to_markdown()),
    },
)
```

Dagster plots numeric metadata over time automatically. Six weeks in you have a chart
of row counts and null rates per run without having built one - and that is your
freshness monitor.

The phase-one run log captures the same fields for exactly this reason. When you
migrate, the metadata dict is already assembled; it changes destination, not content.

---

## Asset checks

The freshness check from [phase 02](02-duckdb-and-the-lock-problem.md#exercise-21---the-stale-run-trap)
becomes:

```python
@asset_check(asset=raw_matches)
def matches_are_fresh(context, raw_matches):
    latest = raw_matches["date"].max()
    age_days = (date.today() - latest).days
    in_season = SEASON_START <= date.today() <= SEASON_END
    return AssetCheckResult(
        passed=(age_days <= 10) or not in_season,
        metadata={"latest_match": str(latest), "age_days": age_days},
    )
```

The body is the phase-one function unchanged. Only the decorator and the result type
differ.

---

## Layout when you get here

The guide's original layout, for reference when you migrate:

```
usl/
  defs.py                 # Dagster definitions
  assets/
    raw.py                # API -> raw tables
    weather.py            # Open-Meteo -> raw_weather
    staging.py            # SQL transforms
    marts.py              # feature table
    models.py             # train, predict, log metrics
```

The phase-one package keeps `ingest/`, `load/`, `transform/`, `features/`, `models/`,
and `export/` as they are. `assets/` sits alongside them and imports from them - the
assets are a thin orchestration wrapper, not a home for logic. If a Dagster asset
contains business logic that is not also callable from `python -m usl.run`, the
migration went wrong.

---

## The DuckDB question, deferred with it

Dagster running assets concurrently makes the single-writer problem from
[phase 02](02-duckdb-and-the-lock-problem.md) worse rather than better - two assets
materialising in parallel against one DuckDB file is the same lock contention with more
ways to hit it. Whatever you decided in phase one has to hold under concurrency before
you turn parallelism on, or you constrain the graph to serial execution and say why.
Worth thinking about before you migrate, not after.

---

## How it landed

```
usl/
  defs.py                 # Definitions: assets, checks, the weekly job and schedule
  assets/
    resources.py          # DuckDBResource (the phase-02 lock guard) and the run-log bridge
    raw.py                # raw_matches, the five reference CSVs, ref_config
    weather.py            # raw_weather (phase 12)
    sql.py                # one asset per SQL model, one asset check per check function
    models.py             # trained_models: a multi-asset with the five output tables
    export.py             # tableau_extracts
```

```
make install-dagster     # pip install -e ".[dagster]"
make dagster             # dagster dev -m usl.defs, UI on http://localhost:3000
```

**Migration was decoration, as promised.** Every asset body is one call into
`usl.load`, `usl.transform`, `usl.models`, `usl.export` or `usl.weather` - the
same call `python -m usl.run` makes - plus a metadata dict. The check functions
became asset checks through one factory: `AssetCheckResult(passed=result.passed,
metadata=result.metadata)`. The test that the SQL files read exactly the tables
each asset declares as upstream keeps the lineage graph honest.

**The run log is still written.** Each asset records itself as a stage of the
Dagster run under the Dagster run id, and each asset check writes its row to
`check_log`, so the Tableau tracker strip reads the same tables whichever
scheduler is in charge. Dagster's own history sits on top rather than replacing
that.

**Checks block by tier.** Staging checks hang off `stg_matches`, intermediate off
`int_standings`, mart off `mart_match_features`, all `blocking=True`. A failing
staging check stops every asset downstream of `stg_matches` in the same run,
which is the phase-one rule - collect within a tier, stop between tiers - with
Dagster doing the stopping. A check may name upstream assets as extra
dependencies but never a downstream one; that is a cycle, and the reason the
mart checks read nothing beyond the mart.

**The DuckDB question, answered.** Serial. `Definitions(executor=in_process_executor)`:
one asset at a time, in dependency order, in one process, each opening and
closing its own connection through the phase-02 lock guard. The pipeline finishes
in seconds, so parallelism would buy nothing and cost the single-writer problem
again with more ways to hit it.

**The schedule** is `weekly_tuesday`, `config.SCHEDULE_CRON` (`0 6 * * 2`) in
`config.SCHEDULE_TZ`. Turning it on in the UI is the moment to delete the
scheduled task; running both is two writers on one file.

**What is deliberately not there.** No partitions (the data is one table per
model, rebuilt whole), no IO managers (DuckDB is the store), no sensors. The
guide's layout put staging and marts in separate files; they are one file here
because they are one factory.
