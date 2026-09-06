# SQL layer

One file per model, materialised by `usl/transform/runner.py` in the order listed
in `MODELS`. SQL lives in files rather than Python string literals: syntax
highlighting, diffs that read, and the ability to paste a file into a DuckDB CLI
to debug it.

Each file is a single `SELECT`. The runner wraps it in
`CREATE OR REPLACE TABLE <name> AS ...`, which makes every model below raw
idempotent for free.

| File | Tier | Grain | Doc |
|---|---|---|---|
| `stg_clubs.sql` | Staging | One row per club-season, with conference and display name | [phase 03](../../docs/phases/03-club-name-consistency.md) |
| `stg_matches.sql` | Staging | One row per raw match, typed, with canonical club ids; nothing dropped | [phase 03](../../docs/phases/03-club-name-consistency.md), [phase 05](../../docs/phases/05-sql-layer.md) |
| `int_standings.sql` | Intermediate | One row per club per conference match date (plus a snapshot the day after the last fixture): the table as of that morning | [phase 04](../../docs/phases/04-standings-as-of-match-date.md) |
| `int_stakes.sql` | Intermediate | One row per `int_standings` row: playoff and relegation lines, `is_mathematically_live`, elimination date | [phase 06](../../docs/phases/06-features.md), [build decisions](../../docs/reference/build-decisions.md) |
| `mart_match_features.sql` | Mart | One row per match, played or not, exactly `definitions.mart_columns()` | [phase 06](../../docs/phases/06-features.md) |
| `mart_decay_curve.sql` | Mart | One row per `matches_since_elimination >= 0`, with `n` | [phase 06](../../docs/phases/06-features.md), exercise 6.3 |

`raw_matches` has no file - it is written by the loader, not by SQL, because it
upserts rather than rebuilding. Raw accumulates; everything below it is derived
and disposable.

Tunables reach the static SQL through the one-row `ref_config` table (match
timezone, COVID window, relegation assumption, playoff fallback) that
`usl/transform/reference.py` builds from `usl/config.py`. Nothing is
string-formatted into SQL. The hand-maintained CSVs under `usl/ref/` are loaded
by the same module with every column as `VARCHAR` and every value
whitespace-normalised; the SQL casts where it needs numbers.

Checks run after each tier (`usl/transform/checks.py`): six on staging, one on
intermediate, two on the mart. Failures are collected within a tier and stop
the run between tiers; every result, pass or fail, is written to `check_log`.

## The rule that matters most

Every window function that looks backwards must exclude the current row:

```sql
ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
```

`AND CURRENT ROW` - which is also SQL's default frame when you write none - leaks
the match you are predicting into its own features. It does not raise. It shows
up as suspiciously good validation error, which is easy to mistake for success.

`int_standings` and the lag features in `mart_match_features` use the
equivalent two-step form: running totals computed *including* each row, then a
strict `ASOF` join (`grid.date > running.date`) that hands every row only what
came strictly before its date. Same rule, and it also gives a date the club
does not play - or a fixture not yet played - its carried-forward values, which
the window form alone cannot. `checks.no_future_leakage` recomputes
`pts_before` by a third method and compares, because nothing else would notice.
