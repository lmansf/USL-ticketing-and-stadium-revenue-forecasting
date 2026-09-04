# Phase 05 - The SQL layer

**Goal.** Three tiers, genuinely separate, each one re-runnable without re-fetching
anything upstream.

**MVP cut.** Two tiers collapsed into one file. See
[docs/mvp/02-mvp-sql-and-features.md](../mvp/02-mvp-sql-and-features.md).

**Files.** `usl/sql/*.sql`, `usl/transform/runner.py`

---

## The tiers

| Tier | Table | Job |
|---|---|---|
| Raw | `raw_matches` | Exactly as the API returned it. Never edited. |
| Staging | `stg_clubs`, `stg_matches` | Types, canonical names, one row per match. |
| Intermediate | `int_standings` | Conference position as of each match date. |
| Mart | `mart_match_features` | One row per match, model-ready. |

Keep them genuinely separate. The discipline is the point, and it buys three specific
things:

- **Raw is a cache of the internet.** You can rebuild every downstream table without
  hitting the source again, which is what makes iterating on the SQL free.
- **Staging is where cleaning is reviewable.** A type coercion in a `.sql` file shows
  up in a diff. The same coercion in a pandas call inside the ingest client does not.
- **The mart is the only thing the model sees.** One row per match, all features
  present, no joins left to do at train time. If the model layer is doing joins, the
  mart is not finished.

The temptation is to shortcut - to compute one feature in the ingest client because it is
easier there, or to have the model reach back to `stg_matches` for one column. Both
work, and both cost you the ability to reason about where a number came from.

---

## Running the SQL

`usl/transform/runner.py` executes the `.sql` files in dependency order against the
DuckDB connection. Keep the SQL in files rather than in Python string literals:
syntax highlighting, diffs that read, and the ability to paste a file into a DuckDB
CLI to debug it.

### Exercise 5.1 - Order and idempotency

The runner has to execute four files in the right order, and running it twice in a row
must produce the same tables it produced once. Decide how the order is determined and
how each model materialises.

<details>
<summary>Solution</summary>

Declare the order explicitly rather than inferring it. Four models is not enough to
justify a dependency parser, and an explicit list is something a reader can check:

```python
MODELS = [
    "stg_clubs",
    "stg_matches",
    "int_standings",
    "mart_match_features",
]

def run_sql_layer(con) -> dict[str, int]:
    counts = {}
    for name in MODELS:
        sql = (SQL_DIR / f"{name}.sql").read_text()
        con.execute(f"CREATE OR REPLACE TABLE {name} AS {sql}")
        counts[name] = con.sql(f"SELECT count(*) FROM {name}").fetchone()[0]
        log.info("materialised %s rows=%s", name, counts[name])
    return counts
```

`CREATE OR REPLACE TABLE ... AS` makes every staging and downstream model idempotent
for free - it is a full rebuild each time, and at this data size a full rebuild costs
nothing. Note that this is the opposite of the `raw_matches` strategy in
[phase 01](01-ingest-to-raw.md), which upserts because it must not lose history that
the source no longer serves. Raw accumulates; everything below it is derived and
disposable. Being able to explain that distinction is worth more than either
implementation.

Return the row counts so the caller can log them. That per-model count is the second
signal described in [phase 03](03-club-name-consistency.md#row-count-logging).
</details>

---

## Checks between tiers

Checks live in `usl/transform/checks.py` as plain functions returning a `CheckResult`.
They run after the tier they check, and the runner records every result to the run log
whether it passed or not.

The set this project expects:

| Check | Tier | Fails when |
|---|---|---|
| `matches_are_fresh` | staging | Latest match is stale during the season |
| `all_clubs_mapped` | staging | Any `home_club_id` or `away_club_id` is null |
| `row_count_preserved` | staging | Staging row count differs from raw row count |
| `one_row_per_match` | staging | `match_id` is not unique |
| `no_future_leakage` | intermediate | Any `int_standings` row uses a result on or after its own date |
| `features_not_null` | mart | A model feature column contains nulls beyond the allowed set |
| `mart_matches_staging` | mart | Mart row count differs from playable staging matches |

Two of these deserve a note. `no_future_leakage` is the one that catches the mistake
in [phase 04](04-standings-as-of-match-date.md) that no other check would find.
`features_not_null` has a deliberate allowed set, because some nulls are correct - a
club's first ever home match has no `last_home_gate`. Which nulls are legitimate is a
decision you make once and encode, and it is demo scenario D4.

---

## Exercise 5.2 - Fail fast or collect

A run hits three failing checks. Should it stop at the first, or run them all and
report the set?

<details>
<summary>Solution</summary>

Collect within a tier, stop between tiers.

Running every check in a tier and reporting all failures at once means one run tells
you everything wrong at that level - three unmapped clubs and a row-count drop are
probably the same root cause, and seeing them together tells you that. Stopping at the
first means three runs to learn three things.

Between tiers, stop. There is no value in computing a mart on staging data you already
know is broken, and doing so produces a second wave of failures that are all
downstream artefacts of the first.

```python
def run_tier(con, models, checks) -> None:
    materialise(con, models)
    results = [c(con) for c in checks]
    for r in results:
        log_check_result(con, r)
    failed = [r for r in results if not r.passed]
    if failed:
        raise CheckFailure(f"{len(failed)} check(s) failed: {[r.name for r in failed]}")
```

Log every result, not only the failures. A check that has passed for six weeks and
starts failing is a signal; a check that only writes a row when it fails gives you no
baseline to notice that against.
</details>

---

## What "done" looks like

- `python -m usl.run transform` rebuilds staging, intermediate, and mart from raw.
- Running it twice produces identical tables.
- Every check result, pass or fail, lands in the run log.
- `tests/test_sql_layer.py` passes.

Next: [phase 06 - Features](06-features.md).
