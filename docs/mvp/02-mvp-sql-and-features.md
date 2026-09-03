# MVP 02 - SQL and features

**Goal.** Raw to a model-ready table in two SQL steps, with league-wide rank and a thin
feature set.

**Extends into:** [phase 03](../phases/03-club-name-consistency.md),
[phase 04](../phases/04-standings-as-of-match-date.md),
[phase 05](../phases/05-sql-layer.md),
[phase 06](../phases/06-features.md)

---

## Two steps, not four

The full track keeps raw, staging, intermediate, and mart genuinely separate. The MVP
collapses to two: `stg_matches` and `mart_match_features`, with the standings
calculation as a CTE inside the mart rather than its own table.

This is the cut that costs you the most later, so know what you are giving up:
debugging a wrong rank means reading a CTE inside a longer query rather than selecting
from `int_standings` directly. With one season it is manageable. With nine it is not,
which is why the full track splits it.

Keep both files in `usl/sql/` regardless. SQL in files rather than Python string
literals is free and pays back immediately.

---

## Club aliases - not optional

Build `usl/ref/club_aliases.csv` for your one season, and make the join fail on
anything unmapped:

```sql
LEFT JOIN club_aliases h ON r.home_raw = h.raw_name
```

then

```python
if unmapped:
    raise ValueError(f"unmapped clubs - add to club_aliases.csv: {unmapped}")
```

`LEFT JOIN` plus a null check, not `INNER JOIN`. The inner join drops the rows and
tells you nothing; this hands you the exact strings to paste into the CSV.

One season is twelve to fifteen clubs, so the file is small and this takes ten minutes.
Doing it now means the file already exists when you backfill the other eight seasons
and it grows rather than gets invented.

---

## Standings - league-wide for now

The full track ranks within conference, because that is the rank a fan reacts to and
the one that will determine relegation. The MVP ranks league-wide, which skips the
conference mapping problem entirely - see
[phase 04](../phases/04-standings-as-of-match-date.md#conference-not-league-wide) for
why that is a real cut and not a free one.

The part that is not negotiable is the window frame:

```sql
SUM(points) OVER (
    PARTITION BY season, club_id ORDER BY date
    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
) AS pts_before
```

`AND 1 PRECEDING`, not `AND CURRENT ROW`. Including the current match leaks its result
into the features that are supposed to predict its attendance. This is the single most
common way a project like this quietly becomes worthless, and the symptom is
suspiciously good validation error rather than an error message.

Rank by points, then goal difference, then goals for. `COALESCE` the first match of the
season to zeros.

---

## Features - the thin set

Enough to answer the headline question and nothing more:

- `day_of_week`, `month`, `is_weekend`, `is_midweek`
- `last_home_gate`, `home_gate_ma3`
- `opponent_club_id`
- `rank_before`, `opponent_rank_before`, `rank_gap`

The first three groups are the baseline model. The last group is the pro-rel model's
addition. That split is the experiment, and it is why even the MVP keeps
`usl/features/definitions.py` as explicit lists rather than selecting columns inline.

Same window-frame rule applies to the lag features:
`ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING`, upper bound exclusive of the current row.

---

## COVID

If your chosen season is 2020 or 2021, pick a different one for the MVP. Attendance
from that period is not demand signal, and handling it properly
([phase 06](../phases/06-features.md#covid)) is a distraction from getting the pipeline
running.

---

## Exercise M2.1 - Sanity-check the rank

Before you train anything, verify the standings calculation against something you can
check by hand.

<details>
<summary>Solution</summary>

Two checks, both quick, and the second one catches things the first does not.

**Final table.** Take the last matchday of your season and compare your computed
standings against the published final table. Points and goal difference should match
exactly. If they do not, the usual causes are draws scored wrong, matches with null
scores not filtered out, or the unpivot dropping one side of each fixture.

**Second match.** Pick one club and check that its `pts_before` on its second match of
the season equals the points it earned in its first. This is the leakage test in its
smallest form - if `pts_before` on match two already includes match two's result, the
number will be wrong in an obvious direction.

Do both by hand once. It takes ten minutes and it is the difference between trusting
the rest of the project and not.
</details>

---

## Done when

- `mart_match_features` has one row per match with no unexpected nulls.
- Final-matchday standings match the published table.
- A club's `pts_before` on match two equals its match-one points.

Next: [MVP 03 - Both models](03-mvp-models.md).
