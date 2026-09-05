# MVP 1

Workspace for the first pass at the MVP. Reference docs live under
[`docs/mvp/`](../../../docs/mvp/) and win any disagreement with this folder.

| File | What |
|---|---|
| `mvp02_sql_and_features.py` | [MVP 02](../../../docs/mvp/02-mvp-sql-and-features.md) as one runnable file on DuckDB |

---

## Run it

No data, no API key, no subscription needed:

```
python "usl/experiments/MVP 1/mvp02_sql_and_features.py" --seed-demo
```

That builds a synthetic 8-club double round robin in memory, runs the real SQL over
it, and verifies the result against an independent pure-Python recomputation of the
final table. **The point is to prove the SQL is correct before real data exists** - so
when MVP 01 lands actual matches, a failure is a data problem rather than a
"is my window frame wrong?" problem.

Against real data once `raw_matches` is populated:

```
python "usl/experiments/MVP 1/mvp02_sql_and_features.py" --db data/usl.duckdb
```

Options: `--aliases` to point at a different `club_aliases.csv`, `--show N` to change
how many mart rows print, `--show 0` for none.

Expected output ends with `all checks passed`. Anything else is a real failure with a
named cause.

---

## What it builds

```
raw_matches  ->  stg_matches  ->  mart_match_features
```

Two SQL steps, not four. Standings live as a CTE inside the mart rather than as their
own `int_standings` table. That is the MVP's biggest cut and the one that costs most
later: debugging a wrong rank means reading a CTE inside a longer query instead of
selecting from a table. Fine with one season, not with nine, which is why the full
track splits it.

Both statements are module constants at the top of the file, so you can read the SQL
without reading the Python.

**Cuts taken, all deliberate:**

- League-wide rank, not conference rank. Skips the conference mapping problem entirely
  - see [phase 04](../../../docs/phases/04-standings-as-of-match-date.md#conference-not-league-wide)
  for why that is a real cut and not a free one.
- Thin feature set: calendar, two lags, opponent, three rank features.
- No COVID handling. Do not point this at 2020 or 2021.

---

## The checks, and why each one exists

Exercise M2.1 says to sanity-check the rank by hand. These are that, as assertions.

| Check | Catches |
|---|---|
| `all_clubs_mapped` | An unmapped club. Names the exact strings, so fixing it is a paste not a hunt |
| `row_count_preserved` | A mapping that points two clubs at one `club_id` - which produces no nulls, so the check above misses it |
| `one_match_per_club_per_date` | A club appearing twice on one date |
| `first_match_is_zero` | The window's NULL on a partition's first row propagating into features |
| `no_leakage` | `pts_before` on match two not equalling match one's points |
| `final_table` | Draws scored wrong, null-score matches not filtered, the unpivot dropping one side |

Two are worth dwelling on.

**`no_leakage`** is the one that matters most. Every backward-looking window in the
file ends at `1 PRECEDING`, never `CURRENT ROW` - which is also SQL's default frame if
you write none. Including the current match leaks its own result into the features
meant to predict its attendance. It does not raise. It shows up as suspiciously good
validation error, which is easy to mistake for success. This check is the smallest
form that catches it.

**`one_match_per_club_per_date`** exists because writing this file produced exactly
that bug. The first demo scheduler shuffled fixtures and chunked them into matchdays,
which let a club play twice on one date. Two things broke silently: the join to
standings fanned out (56 matches became 151 mart rows), and the running-total window,
which orders by date alone, had an arbitrary frame boundary between a club's two
matches that day. Real fixture lists rarely violate this, but a doubleheader, a
backfill that ingested a season twice, or a date column that lost its time component
all would. The demo now uses the circle method so every club plays once per matchday.

`final_table` recomputes points in pure Python rather than in SQL, deliberately - a
check written in the same language as the thing it checks tends to inherit its bugs.

---

## Do the checks actually fire?

A check that cannot fail is decorative. Each one was mutation-tested - break one thing,
confirm the right check catches it - and these are the results.

| Injected bug | Caught by | Silent in |
|---|---|---|
| A club missing from `club_aliases.csv` | `all_clubs_mapped` | - |
| `LEFT JOIN` swapped for `JOIN` | `row_count_preserved` (56 -> 42) | **`all_clubs_mapped`** |
| Two clubs mapped to one `club_id` | `one_match_per_club_per_date` | **`all_clubs_mapped`** |
| The same match ingested twice | `one_match_per_club_per_date` | `row_count_preserved` |
| Window frame changed to `CURRENT ROW` | `no_leakage` | `first_match_is_zero` |

The two bold cells are the reason there is more than one check.

**`LEFT JOIN` to `JOIN` is the one to internalise.** With an inner join the unmapped
rows are not null - they are *gone*. `all_clubs_mapped` scans for nulls, finds none,
and reports success on a staging table that quietly lost a quarter of the season. Only
the row count catches it. That is the silent-data-loss failure the guide keeps warning
about, reproduced here in fourteen rows.

Two honest limitations:

- `first_match_is_zero` goes vacuous under the `CURRENT ROW` mutation - its `WHERE
  played_before = 0` matches nothing once the frame includes the current row. It is not
  a leakage check and does not substitute for one. `no_leakage` is what catches that.
- `final_table` was only confirmed to pass on good data. Making it fail requires
  corrupting the SQL and the Python recomputation differently, which is worth doing if
  you change the points logic.

---

## Findings

Fill in as you go. This is the part worth reviewing.

### Demo run

| | |
|---|---|
| Date run | |
| All checks passed? | |
| Anything surprising | |

### Real data run

| | |
|---|---|
| Date run | |
| Season | |
| `raw_matches` rows | |
| `stg_matches` rows | |
| `mart_match_features` rows | |
| Unmapped clubs found | |
| Final table matched published table? | |
| Nulls in `last_home_gate` / `home_gate_ma3` (should equal club count) | |

### Attendance gate

Not part of this script, but it gates everything. From the repo root:

```
python scripts/check_attendance_coverage.py                    # free, EPL example
python scripts/check_attendance_coverage.py --season-id <usl>  # the one that counts
```

| | Example key | Real USL season |
|---|---|---|
| Date run | | |
| Field found | | |
| Share populated | | |
| Median | | |
| Verdict | | |

An EPL pass proves the schema supports attendance. It does **not** prove FootyStats
holds it for USL - gate figures are far better covered for the major European leagues.
Only the right-hand column decides the project.

### Open items for MVP 3

-

---

## Not here, deliberately

- **Conference rank.** [Phase 04](../../../docs/phases/04-standings-as-of-match-date.md).
- **The models.** [MVP 03](../../../docs/mvp/03-mvp-models.md) - both of them, since the
  comparison is the headline question.
- **The DuckDB lock.** The MVP answer is "close Tableau first", which stops being
  adequate at [MVP 05](../../../docs/mvp/05-mvp-schedule.md) where nobody is present at
  06:00 on a Tuesday. It is the one exercise in the guide with no worked solution.
