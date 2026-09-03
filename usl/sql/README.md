# SQL layer

One file per model, materialised by `usl/transform/runner.py` in the order listed
in `MODELS`. SQL lives in files rather than Python string literals: syntax
highlighting, diffs that read, and the ability to paste a file into a DuckDB CLI
to debug it.

Each file is a single `SELECT`. The runner wraps it in
`CREATE OR REPLACE TABLE <name> AS ...`, which makes every model below raw
idempotent for free.

| File | Tier | Doc |
|---|---|---|
| `stg_clubs.sql` | Staging | [phase 03](../../docs/phases/03-club-name-consistency.md) |
| `stg_matches.sql` | Staging | [phase 03](../../docs/phases/03-club-name-consistency.md), [phase 05](../../docs/phases/05-sql-layer.md) |
| `int_standings.sql` | Intermediate | [phase 04](../../docs/phases/04-standings-as-of-match-date.md) |
| `mart_match_features.sql` | Mart | [phase 06](../../docs/phases/06-features.md) |

`raw_matches` has no file - it is written by the loader, not by SQL, because it
upserts rather than rebuilding. Raw accumulates; everything below it is derived
and disposable.

## The rule that matters most

Every window function that looks backwards must exclude the current row:

```sql
ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
```

`AND CURRENT ROW` - which is also SQL's default frame when you write none - leaks
the match you are predicting into its own features. It does not raise. It shows
up as suspiciously good validation error, which is easy to mistake for success.
