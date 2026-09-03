# Phase 06 - Features

**Goal.** `mart_match_features`, one row per match, every feature both models need,
and an explicit written line between what is measured and what is merely instrumented.

**MVP cut.** Calendar, lags, and `rank_before` only. Enough to answer the headline
question, nothing more. See
[docs/mvp/02-mvp-sql-and-features.md](../mvp/02-mvp-sql-and-features.md).

**Files.** `usl/sql/mart_match_features.sql`, `usl/features/definitions.py`

---

## Three families

**Calendar and lag.** `day_of_week`, `month`, `is_weekend`, and the club's own history:
last home gate, rolling mean of the last three and last five home gates,
same-fixture-last-season attendance. Weather fields join this family in
[phase two](12-phase-two-weather.md); phase one ships without them.

**Match context.** `opponent_club_id`, `is_derby` (hand-flagged in
`usl/ref/derbies.csv`), `is_midweek`, `matches_remaining`, `is_season_opener`,
`is_final_home_match`.

**Pro-rel.** The differentiated ones: `rank_before`, `points_from_playoff_line`,
`points_from_relegation_line`, `is_mathematically_live`, `opponent_rank_before`,
`rank_gap`.

The three families are not just documentation. `usl/features/definitions.py` holds
them as explicit lists, and [phase 07](07-two-models.md) builds its two models by
selecting from those lists. The families *are* the experiment design.

---

## Exercise 6.1 - Lag features without leakage

Compute a club's rolling mean of its last three home gates. The trap is the same one as
[phase 04](04-standings-as-of-match-date.md) and it is just as easy to walk into.

<details>
<summary>Solution</summary>

```sql
SELECT
    match_id,
    home_club_id,
    date,
    LAG(attendance) OVER w AS last_home_gate,
    AVG(attendance) OVER (
        PARTITION BY home_club_id ORDER BY date
        ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING
    ) AS home_gate_ma3,
    AVG(attendance) OVER (
        PARTITION BY home_club_id ORDER BY date
        ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
    ) AS home_gate_ma5
FROM stg_matches
WINDOW w AS (PARTITION BY home_club_id ORDER BY date)
```

`AND 1 PRECEDING` on the upper bound, not `CURRENT ROW`. The default window frame in
SQL is `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`, which includes the row
you are predicting. Writing `ROWS BETWEEN 3 PRECEDING AND CURRENT ROW` gives you a
model that predicts attendance using attendance, and an MAE that looks spectacular.

Two design calls worth making explicitly. These windows cross season boundaries -
a club's first home match of 2024 gets a moving average from late 2023. Decide whether
that is signal (the club's recent form and support level carry over) or noise (a
five-month gap and a different squad), and add `PARTITION BY home_club_id, season` if
you decide the latter. Second, these windows include COVID-affected matches unless you
exclude them upstream, which would drag a 2021 club's moving average down toward
empty-stadium figures. Handle COVID before you compute lags, not after.
</details>

---

## Exercise 6.2 - matches_remaining and is_mathematically_live

`is_mathematically_live` asks whether a club can still reach the playoff line. Compute
it. The naive version is wrong in a way that matters.

<details>
<summary>Solution</summary>

The naive version:

```sql
(pts_before + 3 * matches_remaining) >= playoff_line_points AS is_mathematically_live
```

This compares against the playoff line *as it stands now*, but the clubs occupying
those positions also have matches left. A club six points back with three to play is
not live if the club above it needs only one more win.

The honest version compares against the minimum points the club currently holding the
last playoff position is guaranteed to finish on - which is its current points, since
in the worst case it loses everything:

```sql
(pts_before + 3 * matches_remaining) > line_club_pts_before AS is_mathematically_live
```

Strictly greater, because a tie goes to the tiebreaker and "live" should not depend on
goal difference arithmetic you have not done.

This is still an approximation - true mathematical elimination accounts for head-to-head
fixtures between rivals, where one of them must drop points. That calculation is a
constraint solver, not a SQL expression. Use the approximation, and write down that it
is one. For the dead-rubber decay curve in the next section, the approximation is
conservative in the right direction: it declares clubs live slightly longer than
reality, so `matches_since_elimination` is if anything an undercount.
</details>

---

## The dead-rubber counterfactual

Pro-rel does not start until 2028, so `points_from_relegation_line` has no ground
truth. But two things in the current data are measurable today and carry most of the
argument.

**1. Playoff position as a partial proxy.** `points_from_playoff_line` and
`is_mathematically_live` capture stakes-driven demand right now. Use them to establish
the load-bearing assumption behind all of USL's 2028 forecasting: that table position
affects attendance at all.

Be precise about the limit. A playoff race measures *upside* stakes; relegation
measures *downside*, existential stakes, and European evidence suggests those do not
move fans identically. So this is evidence the mechanism exists - not a calibrated
estimate of the relegation effect size. Say it in those terms.

**2. The bottom of the table - the stronger measurement.** Clubs mathematically
eliminated in September are, today, playing dead rubbers to apathetic crowds. In 2028,
those same clubs are the ones fighting relegation.

So measure attendance decay on eliminated-club home matches across all nine seasons:
how far does the gate fall, how fast, and does it recover. That is the
**counterfactual baseline** - what these fixtures drew when nothing was at stake. When
relegation arrives, the gap between that baseline and observed attendance *is* the
relegation effect, isolated.

Concretely: a `matches_since_elimination` feature plus a decay curve on eliminated-club
home gates. It is a real finding on real data, and it is the number that becomes most
valuable the moment pro-rel lands.

### Exercise 6.3 - The decay curve

Produce the curve: mean attendance on eliminated-club home matches by
`matches_since_elimination`, across all nine seasons, controlled well enough to be
worth showing.

The word doing the work in that sentence is "controlled".

<details>
<summary>Solution</summary>

Raw mean by `matches_since_elimination` is confounded three ways, and each one pushes
in the same direction, which is the dangerous kind:

- Eliminated clubs are bad clubs, and bad clubs draw smaller crowds all season. You
  would see a low number even with no decay at all.
- Elimination happens late, so `matches_since_elimination` correlates with late-season
  dates, which have their own attendance pattern.
- Clubs eliminated earliest are the worst clubs, so the tail of the curve is a
  different, worse population than the head.

Index against the club's own baseline rather than comparing across clubs:

```sql
SELECT
    matches_since_elimination,
    count(*) AS n,
    avg(attendance / club_season_home_mean_before_elimination) AS index_vs_own_baseline
FROM mart_match_features
WHERE matches_since_elimination >= 0
  AND NOT is_covid_affected
GROUP BY 1
ORDER BY 1
```

Each club is now compared only to itself, which removes the first confounder entirely
and most of the third. Report `n` alongside every point - the tail thins out fast, and
a curve whose last point rests on four matches should look like it rests on four
matches when it is plotted.

For the residual date effect, the cleanest control available in this dataset is to
compare eliminated clubs against still-live clubs *in the same week*, which nets out
whatever late-season pattern applies to everyone. That is a stronger claim and it is
worth doing if the sample supports it.
</details>

---

## COVID

2020 had restricted and empty stadiums. Attendance figures from that period are not
demand signal. Add an `is_covid_affected` flag and a `DROP_COVID` switch - default it
on for training, and be able to flip it to show the difference.

The flag is a date range, and the boundaries are a judgement call rather than a fact.
Restrictions eased at different times in different markets, and some 2021 matches were
capacity-limited too. Put the range in `usl/config.py` where it is visible and
changeable, not inline in SQL, and say in the README that it is a range you chose.

Being able to show the model's error with and without the exclusion, on demand, is
worth more than getting the boundary exactly right.

---

## The honesty note

Draw the line in three parts, and be explicit about which is which. This paragraph
belongs in the README, in the dashboard, and in the video, because burying it is worse
than leading with it and because it is the part most likely to come up in an interview.

- **Measured.** Calendar, lag, and match-context features. Also the dead-rubber decay
  curve - real attendance on eliminated-club home matches across nine seasons. These
  are findings.

- **Measured, but a partial proxy.** `points_from_playoff_line`,
  `is_mathematically_live`, `rank_before`. These show table position affects
  attendance. They do *not* size the relegation effect - upside stakes are not
  downside stakes.

- **Instrumented, unvalidated.** `points_from_relegation_line`. No relegation exists in
  the data, so it has no ground truth. Build it, log its importance, and label it in
  both the dashboard and the README as a forward-looking instrument rather than a
  predictor.

`usl/features/definitions.py` carries this classification as data - each feature
tagged `measured`, `proxy`, or `instrumented` - so the dashboard can colour by it and
so the labelling cannot drift away from the feature list.

---

## What "done" looks like

- `mart_match_features` has one row per playable match and no unexpected nulls.
- Every feature in `definitions.py` exists as a column, and every column is in
  `definitions.py`. A test enforces both directions.
- `DROP_COVID` changes the training set size and nothing else.
- The decay curve renders and each point carries its `n`.
- `tests/test_features.py` passes.

Next: [phase 07 - Two models](07-two-models.md).
