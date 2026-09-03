# Phase 04 - Standings as of match date

**Goal.** For every match, where each club sat in its conference table *going into*
that match: points, played, goal difference, and rank.

**Why this gets its own phase.** It is not a lookup. There is no table on the source
site for "the standings on 14 June 2019" - only the standings now. You reconstruct it
as a running calculation over match history, and it is the core of the pro-rel thesis.
Also the hardest SQL in the project, which is why the build order puts it before the
fun parts.

**MVP cut.** League-wide rank instead of conference rank, which removes the conference
mapping problem entirely. See
[docs/mvp/02-mvp-sql-and-features.md](../mvp/02-mvp-sql-and-features.md).

**Files.** `usl/sql/int_standings.sql`

---

## Two things that will bite you

**Point-in-time only.** Use results strictly *before* the match date. Include the match
itself and you have leaked the result into the features that are supposed to predict
its attendance. This is the single most common way a model like this quietly becomes
worthless, and it does not announce itself - it shows up as suspiciously good
validation error.

**Tie-breaking.** Rank by points, then goal difference, then goals for. Get this stable
or position jitters meaninglessly between rounds and every rank-derived feature
inherits the noise.

---

## Conference, not league-wide

USL Championship uses conference splits, and the table position a fan actually reacts
to is the one in their conference - it is what determines playoff qualification, and
under pro-rel it is what will determine relegation. **This project ranks within
conference.** Write that choice down in the README, because it is exactly the kind of
thing an interviewer probes, and the wrong answer is not "league-wide", it is "I did
not notice there was a choice".

> **Flagged as unresolved.** The build guide does not say where conference membership
> comes from, and it is not a constant - clubs have switched conferences between
> seasons, and the number of conferences has itself changed across the nine seasons.
> `usl/ref/club_conference.csv` is stubbed as a `club_id, season, conference` mapping,
> which is the shape that handles both. Confirm against the source before you fill it
> in, and see [reference/open-questions.md](../reference/open-questions.md#conference-membership).
> Do not infer conference from the standings page of the current season and
> backfill it - that is wrong for any club that moved.

---

## Exercise 4.1 - Reconstruct the table

Build `int_standings` with one row per club per match date: points before, played
before, goal difference before, goals for before, and conference rank before.

Start from the shape of the input. `stg_matches` has one row per match with a home
club and an away club. What shape do you need before a window function can help you?

<details>
<summary>Solution</summary>

Unpivot to one row per club per match, then use a window function.

```sql
-- one row per club per match. No conference here - see the note below.
WITH club_matches AS (
    SELECT season, date, home_club_id AS club_id,
           CASE WHEN home_goals > away_goals THEN 3
                WHEN home_goals = away_goals THEN 1 ELSE 0 END AS points,
           home_goals AS gf, away_goals AS ga
    FROM stg_matches WHERE home_goals IS NOT NULL
    UNION ALL
    SELECT season, date, away_club_id,
           CASE WHEN away_goals > home_goals THEN 3
                WHEN away_goals = home_goals THEN 1 ELSE 0 END,
           away_goals, home_goals
    FROM stg_matches WHERE home_goals IS NOT NULL
),
-- conference is an attribute of the club-season, so it joins per club
with_conference AS (
    SELECT m.*, c.conference
    FROM club_matches m
    JOIN stg_clubs c
      ON c.club_id = m.club_id AND c.season = m.season
),
-- cumulative totals BEFORE each match
running AS (
    SELECT season, conference, club_id, date,
           SUM(points) OVER w AS pts_before,
           SUM(gf - ga) OVER w AS gd_before,
           SUM(gf)     OVER w AS gf_before,
           COUNT(*)    OVER w AS played_before
    FROM with_conference
    WINDOW w AS (
        PARTITION BY season, club_id
        ORDER BY date
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    )
)
SELECT *,
       RANK() OVER (
           PARTITION BY season, conference, date
           ORDER BY pts_before DESC, gd_before DESC, gf_before DESC
       ) AS rank_before
FROM running;
```

`ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` is what enforces point-in-time. It
is the whole exercise. Everything else is bookkeeping.

`COALESCE` the first match of each season to zeros - the window returns null there,
and a null rank propagates into your features.

Note where the conference comes from. It is an attribute of the **club-season**, so it
joins from `stg_clubs` on `(club_id, season)` after the unpivot. Taking it off the
match row instead is the common slip, and it is silent: an interconference fixture
carries one conference value, so whichever club is not in it gets ranked in the wrong
table. That is also why `stg_matches` has no `conference` column - there is no correct
value to put in it.

The join is an inner join on purpose. A club-season missing from `stg_clubs` should
drop out loudly at the row-count check rather than get a null conference and form a
silent third conference of its own under the final `RANK()`.
</details>

---

## Exercise 4.2 - The gap between match dates

The solution above ranks clubs only among those playing on the same date. Two clubs
whose most recent matches were a week apart never get compared, and a club that did not
play on date D has no row for date D at all.

Decide whether that is a problem for your features, and if it is, fix it. State the
choice either way.

<details>
<summary>Solution</summary>

It is a problem, and the symptom is that `rank_before` is not the number a fan would
recognise. On a Wednesday when three of twenty-four clubs play, `RANK()` partitioned by
date returns 1, 2, and 3 - not their actual table positions.

The fix is to rank every club in the conference as of each distinct match date, not
just the ones playing:

```sql
-- cross join every club-season against every match date in that season,
-- carry the last-known cumulative totals forward, then rank the full field
WITH date_grid AS (
    SELECT DISTINCT season, conference, date FROM with_conference
),
club_grid AS (
    SELECT g.season, g.conference, g.date, c.club_id
    FROM date_grid g
    JOIN (SELECT DISTINCT season, conference, club_id FROM with_conference) c
      ON c.season = g.season AND c.conference = g.conference
)
-- then ASOF JOIN each club_grid row to its most recent running total
```

DuckDB has `ASOF JOIN`, which is built for exactly this and is worth learning here:

```sql
SELECT g.*, r.pts_before, r.gd_before, r.gf_before, r.played_before
FROM club_grid g
ASOF LEFT JOIN running r
  ON g.club_id = r.club_id AND g.season = r.season AND g.date >= r.date
```

The cost is a bigger intermediate table. At this data size that is nothing, and the
feature is now the number a fan would actually recognise, which matters when the whole
thesis is about fan response to table position.

If you decide the simpler version is good enough, that is defensible - just document
that `rank_before` means "rank among clubs in action" and be ready to say why.
</details>

---

## Derived stakes features

`int_standings` is the base. The stakes features in
[phase 06](06-features.md) are computed from it:

- `points_from_playoff_line` - points behind the last qualifying position in the
  conference, negative if above it
- `points_from_relegation_line` - the same shape against a relegation cutoff that does
  not exist yet, so this is instrumented and unvalidated by construction
- `is_mathematically_live` - can this club still reach the playoff line given
  `matches_remaining` times three points
- `matches_since_elimination` - the feature the dead-rubber decay curve is built on

Each needs `matches_remaining`, which needs the season's fixture count per club, which
is not constant across nine seasons. Compute it from the schedule rather than
hardcoding it.

---

## What "done" looks like

- `int_standings` has one row per club per relevant date with no null ranks after the
  first matchday.
- A club's `pts_before` on its second match equals the points it earned in its first.
- Rank on the final matchday of a completed season matches the published final table
  for that conference. This is your correctness test and it is worth doing by hand for
  one season.
- `tests/test_standings.py` passes, including the leakage test.

Next: [phase 05 - The SQL layer](05-sql-layer.md).
