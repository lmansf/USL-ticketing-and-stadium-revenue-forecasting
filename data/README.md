# data/

Holds `usl.duckdb` and the scraper's response cache. Both are gitignored: the
database is a build product, rebuildable from the source with `make backfill`,
and the cache is disposable.

Nothing here is a source of truth. The sources of truth are the code, the SQL, and
the four hand-maintained CSVs under `usl/ref/`.

```
data/
+-- usl.duckdb        The database. Single file, single writer
+-- usl.duckdb.tmp    Only present mid-run if you took the swap route in db.py
+-- cache/            Saved HTTP responses, keyed by season
```

`data/cache/` is for development convenience and gets cleared. Fixtures you want
to keep - for tests and demos - belong in `demo/fixtures/`, which is committed.

## The single-writer constraint

DuckDB allows one writer at a time. If Tableau has this file open, Tuesday's
scheduled run cannot write to it, and the failure that matters is the silence
afterwards. See
[docs/phases/02-duckdb-and-the-lock-problem.md](../docs/phases/02-duckdb-and-the-lock-problem.md).

## Rebuilding

```
make clean-db
make backfill
make transform
make train
```

Losing this file costs you the backfill time and the accumulated run history in
`run_log`, `model_metrics`, `predictions`, and `feature_importance`. The backfill
you can redo. The history you cannot - it is a time series that only accumulates
going forward, which is the argument for taking a copy before anything
destructive.
