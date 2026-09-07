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

**Running it for real, once.** `make dagster` without more is a development
server: it keeps its run history in a temporary folder that is deleted when it
exits, and the schedule fires only while it runs. For the live run, once:

```
setx DAGSTER_HOME C:\Users\you\dagster_home     # a folder that stays; open a new terminal afterwards
```

then, in `.env`, `USL_CURRENT_SEASON=2026`, and `make dagster` from the
project folder. In the UI, Materialize all once, so the season in progress is
pulled as today's snapshot and the weather topped up, then turn `weekly_tuesday`
on under Automation. The daemon inside `dagster dev` fires it every Tuesday at
06:00 Central while the terminal is open; a run it missed because the machine
was off is not made up, so the weekly command (`python make.py weekly`) is the
manual catch-up. `dagster-daemon run` and `dagster-webserver` are the same two
halves as separate services when the machine is a server.

**A white screen in the browser** means the page was served and the app did
not render; the server is almost never the problem. In order:

1. The terminal must have printed `Serving dagster-webserver on
   http://127.0.0.1:3000`. Startup takes about thirty seconds, and there is
   nothing to load before that line; a traceback instead of it is the answer.
2. Open `http://127.0.0.1:3000/server_info`. One JSON line with three matching
   versions means the server is fine and the browser is the problem: hard
   refresh (Ctrl+F5), then a private window, since a stale bundle and an
   extension that blocks scripts are the usual causes, then another browser.
3. No JSON, or someone else's page, means another program owns port 3000. Use
   `make dagster DAGSTER_PORT=3050` and open that port instead.
4. `pip show dagster dagster-webserver` must print the same version twice; if
   not, `pip install "dagster==X" "dagster-webserver==X"` with the `dagster`
   version.
5. `python scripts/check_dagster_ui.py` fetches the page and every script it
   names outside any browser and says which one is missing or rewritten. The
   case it was written for: every script 404 and a build of under a hundred
   files, because the venv sits under long folder names and the build's
   longest path passes the 259 characters Windows allows, so pip stopped
   writing the package partway (its own hint about long paths goes by in the
   install output). Enable long paths once or move the project to a short
   path, then reinstall `dagster-webserver`. Exit 0 there and white in
   every browser, private windows included, means
   something on the machine intercepts the page on its way to the browser -
   an antivirus web shield that scans local HTTP, or a browser policy - and
   the fix is its exclusion list, not Dagster. The browser console (F12,
   Console) names it.

**The UI is optional.** Everything the UI does for this project is one CLI
command each, against the same instance in `DAGSTER_HOME`:

```
python -m dagster job execute -m usl.defs -j weekly          # Materialize all: every asset, every check, in order
python -m dagster schedule start -m usl.defs weekly_tuesday  # turn the schedule on
python -m dagster schedule list -m usl.defs                  # ... and see that it is RUNNING
python make.py dagster                                       # keep this running: its daemon fires the schedule
```

The run log and `check_log` are written the same way, under the Dagster run
id, so the tracker strip does not care which door the run came through.

**What is deliberately not there.** No partitions (the data is one table per
model, rebuilt whole), no IO managers (DuckDB is the store), no sensors. The
guide's layout put staging and marts in separate files; they are one file here
because they are one factory.
