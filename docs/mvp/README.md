# MVP track

The fastest defensible version of every technical decision in this project, so you have
something running end to end today rather than in three weeks.

**What the MVP is for.** Getting the shape of the pipeline into your hands. One season
scraped, in DuckDB, through SQL, into two XGBoost models, out to a CSV Tableau can
read, on a Tuesday schedule. Five steps, an afternoon.

**What the MVP is not.** It is not the thing you show someone. The full track exists
because the parts the MVP cuts are the parts that make the project credible.

---

## What it cuts

| | MVP | Full |
|---|---|---|
| Seasons | One, most recent complete | All nine plus current |
| Fetch | Requests plus a disk cache | Retry policy, politeness delays, cache invalidation by season status |
| Schema drift | Assert on column names | Missing raises, extra warns, message names both sides |
| SQL tiers | One file, raw to mart | Three tiers, kept genuinely separate |
| Standings | League-wide rank | Conference rank, tie-broken, full-field via ASOF join |
| Features | Calendar, lags, `rank_before` | Three families, stakes features, decay curve |
| Validation | Single chronological holdout | Expanding-window CV, naive baseline, multi-seed |
| Checks | Unmapped clubs only | Seven checks across three tiers |
| Logging | Run row: status, rows, timestamp | Full run metadata, per-check results |
| Tableau | CSV extract into Public | DuckDB JDBC connector during the Desktop trial |
| Uncertainty | None | Residual band on the drill-down view |
| Demos | None | Four break-and-fix, three working-behaviour |
| Install | `requirements-mvp.txt`, seven packages | `requirements.txt` |

---

## What it refuses to cut

Four things are in the MVP because leaving them out produces something actively
misleading rather than merely incomplete.

**Point-in-time correctness.** Every window function uses
`ROWS BETWEEN ... AND 1 PRECEDING`. A leaky MVP gives you an MAE that looks great and
means nothing, and you will believe it for a week before you find out.

**The club alias mapping with a failing join.** Skipping it does not save you time, it
just moves the cost. An unmapped club drops rows silently and your model trains on a
dataset with a hole in it that nobody sees.

**Idempotency.** `match_id` as a primary key and an upsert on load. Retrofitting this
after you have a database full of duplicates is worse than doing it now, and "run it
twice" is the first thing anyone tries.

**Both models.** The headline question is the comparison. One model is not a smaller
version of this project; it is a different project.

---

## Install

```
pip install -r requirements-mvp.txt
```

Seven packages: `requests` and `lxml` to scrape, `duckdb` and `pandas` to store
and shape, `xgboost` with `scikit-learn` and `numpy` to model. That is the whole
stack between the source site and the CSV Tableau reads.

Two pieces of infrastructure, and both are things you already have: a DuckDB
file on disk, and Tableau Public. Nothing listens on a port, nothing needs an
API key, nothing authenticates. The transform layer is SQL, but it runs inside
DuckDB - Python reads the `.sql` file and hands it over. The scheduler is
Windows Task Scheduler, which is outside Python entirely and is the one place a
failure will not produce a traceback.

`xgboost` is the only heavy install here; it ships a compiled wheel. It is not
optional - the two-model comparison is the headline question.

## The steps

1. [Scrape one season into DuckDB](01-mvp-scrape-to-duckdb.md)
2. [SQL and features](02-mvp-sql-and-features.md)
3. [Both models](03-mvp-models.md)
4. [Tableau Public via CSV](04-mvp-tableau.md)
5. [Schedule it for Tuesday](05-mvp-schedule.md)

---

## Graduating

The MVP is a strict subset. Nothing you write here is thrown away when you move to the
full track - the files are the same files, and each MVP step names the phase doc that
extends it. The order to graduate in, roughly by payoff:

1. Backfill the remaining seasons. Everything downstream gets more interesting with
   nine seasons of history, and the lag features only start working properly with more
   than one.
2. [Conference rank](../phases/04-standings-as-of-match-date.md). The headline question
   is about the rank a fan reacts to, and that is the conference one.
3. [The stakes features and the decay curve](../phases/06-features.md). This is where
   the project stops being a demand model and starts being an argument.
4. [The remaining checks and full run logging](../phases/05-sql-layer.md). Cheap, and
   they are what let you trust the weekly run without watching it.
5. [The demos](../phases/09-break-and-fix.md) and the Tableau Desktop trial. Last,
   because the trial is on a clock.
