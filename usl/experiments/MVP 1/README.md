# MVP 1

Workspace for the first pass at the MVP. Reference docs live under
[`docs/mvp/`](../../../docs/mvp/) and win any disagreement with this folder.

| File | What |
|---|---|
| `mvp02_sql_and_features.py` | [MVP 02](../../../docs/mvp/02-mvp-sql-and-features.md) as one runnable file on DuckDB |
| `write_raw.py` | Fetches and archives a league-matches payload |
| `create_raw_tables.py` | Drift guard, coverage check, and a raw table in `raw.db` |
| `display_raw.py` | Reads it back |
| `league-matches_season_1.json` | The archived response. EPL 2018/19 via the free `example` key |

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

Straight from an archived payload, which is the shortest path from MVP 01 to MVP 02:

```
python "usl/experiments/MVP 1/mvp02_sql_and_features.py" \
    --from-json "usl/experiments/MVP 1/league-matches_season_1.json"
```

That builds `raw_matches` from the payload directly, so nothing has to be staged
through `raw.db` first. It also derives a starter `club_aliases` from the payload -
provider id to a slug of the club's *current* name. Review it by hand before trusting
it: current names are wrong for historical seasons, which is the whole point of
[phase 03](../../../docs/phases/03-club-name-consistency.md).

Options: `--aliases` to point at a different `club_aliases.csv`, `--show N` to change
how many mart rows print, `--show 0` for none.

Expected output ends with `all checks passed`. Anything else is a real failure with a
named cause.

---

## Running it on `raw.db`

**You cannot yet, and the script refuses on purpose.** `raw_table` has `id`,
`home_name`, `away_name`, `stadium_name`, `attendance` - enough to answer "is
attendance populated", not enough to reconstruct a league table. Missing: `season`,
`date`, `home_goals`, `away_goals`. Without dates and goals there are no standings,
and standings are most of MVP 02.

Point `--db` at it and the error names every missing column and what each is for.

### Path 1 - skip `raw.db` (works right now)

```
cd "usl/experiments/MVP 1"
python mvp02_sql_and_features.py --from-json league-matches_season_1.json
```

Builds `raw_matches` from the payload in memory. Nothing to rebuild, nothing to
migrate. Verified: 380 rows, all checks pass.

### Path 2 - carry the extra fields, then `--db` works

Every field is already in the payload - they were just not selected. In
`create_raw_tables.py`, widen `REQUIRED` and the row dict:

```python
REQUIRED = {"id", "season", "date_unix", "homeID", "awayID",
            "homeGoalCount", "awayGoalCount", "attendance",
            "home_name", "away_name", "stadium_name"}

new_row = {
    "match_id":   f"fs:{d['id']}",                  # namespaced provider id
    "season":     int(str(d["season"])[:4]),        # "2018/2019" -> 2018
    "date":       datetime.fromtimestamp(d["date_unix"], timezone.utc).date().isoformat(),
    "home_raw":   str(d["homeID"]),                 # the id, NOT home_name
    "away_raw":   str(d["awayID"]),
    "home_goals": d["homeGoalCount"],
    "away_goals": d["awayGoalCount"],
    "attendance": d["attendance"],
    "stadium_name": d["stadium_name"],
    "ingested_at": datetime.now(timezone.utc),
}
```

Name the table `raw_matches`, and use `CREATE OR REPLACE TABLE` rather than
`CREATE TABLE IF NOT EXISTS` - see the note in Open items about the `last_updated`
column that silently never arrived.

Join on `homeID`, not `home_name`. The payload's name is the club's *current* name, so
a 2017 match would arrive under a 2026 brand.

Then either put a `club_aliases` table in the same database, or pass `--aliases`:

```
python mvp02_sql_and_features.py --db raw.db
python mvp02_sql_and_features.py --db raw.db --aliases ../../ref/club_aliases.csv
```

The script uses a `club_aliases` table already in the database if there is one, and
only falls back to the CSV when there is none or when `--aliases` is passed. Verified
on a rebuilt `raw.db` with these columns: 380 rows, all checks pass.

> **`--db` writes into that file.** It creates `stg_matches` and `mart_match_features`
> in whatever database you point at. Since `raw.db` is committed, that shows up as a
> git diff. Another reason the archive rather than the database is the thing worth
> committing.

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
| Date run | 2026-09-05 |
| All checks passed? | Yes, 56 matches / 8 clubs |
| Anything surprising | Yes - the first demo scheduler let clubs play twice a day. See the checks section above |

### Real data run - EPL 2018/19, free `example` key

| | |
|---|---|
| Date run | 2026-09-05 |
| Source | `league-matches_season_1.json` (season id 1625) |
| `raw_matches` rows | 380 |
| `stg_matches` rows | 380 |
| `mart_match_features` rows | 380 |
| Unmapped clubs found | 0, of 20 |
| Nulls in `last_home_gate` / `home_gate_ma3` | 20 and 20 - exactly one per club, correct |
| **Final table vs published** | **Top 6 match exactly, points and goal difference** |

The published-table check is worth spelling out, because it is Exercise M2.1 done for
real rather than against a fixture:

| Club | Computed | Published 2018/19 |
|---|---|---|
| Manchester City | 98 pts, +72 | 98 pts, +72 |
| Liverpool | 97 pts, +67 | 97 pts, +67 |
| Chelsea | 72 pts, +24 | 72 pts, +24 |
| Tottenham | 71 pts, +28 | 71 pts, +28 |
| Arsenal | 70 pts, +22 | 70 pts, +22 |
| Manchester United | 66 pts, +11 | 66 pts, +11 |

Points and goal difference both, against a table the code has never seen. That is
external validation of the standings reconstruction, not a self-check.

**Still to do on real data:** run this against a USL season. Everything above is EPL.

### Attendance gate

Not part of this script, but it gates everything. From the repo root:

```
python scripts/check_attendance_coverage.py                    # free, EPL example
python scripts/check_attendance_coverage.py --season-id <usl>  # the one that counts
```

| | Example key (EPL 2018/19) | Real USL season |
|---|---|---|
| Date run | 2026-09-05 | |
| Field found | `attendance` | |
| Share populated | **380/380, 100%** | |
| Median | 31,957 | |
| Min / max | 9,980 / 81,332 | |
| Verdict | **PASS** - the field exists and is fully populated | |

Half the gate is now answered: **`league-matches` carries a per-match `attendance`
field, and for the EPL it is 100% populated** with plausible values. No match-detail
call is needed for it.

An EPL pass proves the schema supports attendance. It does **not** prove FootyStats
holds it for USL - gate figures are far better covered for the major European leagues.
Only the right-hand column decides the project.

### Open items for MVP 3

- **Run the attendance gate on a USL season.** The only thing still blocking. EPL
  coverage says nothing about USL - a median of 31,957 is Premier League, and USL gates
  run in the low thousands.
- `raw.db` is committed (536 KB). The project's convention is that databases are build
  products and only `data/raw_archive/` is durable - the JSON here is the thing worth
  keeping. Consider gitignoring `*.db` under experiments.
- `create_raw_tables.py` uses `CREATE TABLE IF NOT EXISTS ... AS SELECT *`, which is not
  idempotent in the sense MVP 01 needs: re-running never updates. It has already bitten
  once - the DataFrame builds a `last_updated` column that is **not** in `raw.db`,
  because the table was created before that column existed and `IF NOT EXISTS` silently
  kept the old schema. `CREATE OR REPLACE`, or a primary key and an upsert, fixes it.
- `write_raw.py` sends `league_id=1625` where 1625 is a season id, and names the file
  `season_1`. Worth reconciling before there are nine of them - phase 00 leans on
  archive filenames being readable enough to tell what you have not pulled yet.

---

## Not here, deliberately

- **Conference rank.** [Phase 04](../../../docs/phases/04-standings-as-of-match-date.md).
- **The models.** [MVP 03](../../../docs/mvp/03-mvp-models.md) - both of them, since the
  comparison is the headline question.
- **The DuckDB lock.** The MVP answer is "close Tableau first", which stops being
  adequate at [MVP 05](../../../docs/mvp/05-mvp-schedule.md) where nobody is present at
  06:00 on a Tuesday. It is the one exercise in the guide with no worked solution.
