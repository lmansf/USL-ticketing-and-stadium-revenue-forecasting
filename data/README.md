# data/

Holds `usl.duckdb`, which is gitignored and disposable, and `raw_archive/`, which
is committed and is not.

That split is the important thing on this page.

```
data/
+-- usl.duckdb        The database. Gitignored. Rebuildable from raw_archive/
+-- usl.duckdb.tmp    Only present mid-run if you took the swap route in db.py
+-- raw_archive/      Every raw API response. COMMITTED. Not regenerable
+-- cache/            Scratch. Gitignored, clearable
```

**`raw_archive/` is the source of truth for data.** The FootyStats subscription runs
for one month; once it lapses this directory is the only copy of the source data in
existence for this project, and no amount of later work regenerates it. See
[data/raw_archive/README.md](raw_archive/README.md) and
[phase 00](../docs/phases/00-data-access-and-the-clock.md).

The other sources of truth are the code, the SQL, and the hand-maintained CSVs under
`usl/ref/` - including `seasons.csv`, which maps each season year to its FootyStats
season id and is itself only discoverable while subscribed.

## The single-writer constraint

DuckDB allows one writer at a time. If Tableau has this file open, Tuesday's
scheduled run cannot write to it, and the failure that matters is the silence
afterwards. See
[docs/phases/02-duckdb-and-the-lock-problem.md](../docs/phases/02-duckdb-and-the-lock-problem.md).

## Rebuilding

```
make clean-db
make backfill      # served from raw_archive/, no subscription needed
make transform
make train
```

Deleting `usl.duckdb` is safe. The backfill replays from `raw_archive/` in seconds and
needs no API key.

What you cannot rebuild:

- **`raw_archive/`**, once the subscription has lapsed. This is why it is committed.
- **The accumulated run history** in `run_log`, `model_metrics`, `predictions`, and
  `feature_importance`. A time series that only grows going forward - take a copy
  before anything destructive.

So `make clean-db` is cheap for the raw data and expensive for the history. If you have
weeks of model metrics you care about, export them first.
