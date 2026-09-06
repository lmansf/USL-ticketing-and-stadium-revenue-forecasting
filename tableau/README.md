# Tableau

Two connection paths, and which one you use depends on which edition you have
running.

| | Tableau Public (free) | Tableau Desktop (14-day trial) |
|---|---|---|
| Connects to | Files only | Files and databases |
| DuckDB | No. The connector will not appear | Yes, via the JDBC connector and `.taco` |
| Use it for | The shipped artifact, and after day 15 | Building the live dashboard, and the video |
| Doc | [MVP 04](../docs/mvp/04-mvp-tableau.md) | [phase 08](../docs/phases/08-tableau.md), [connector setup](../docs/reference/tableau-duckdb-connector.md) |

**Start the trial last.** It is the *second* clock in this project - the
FootyStats subscription is the first and the expensive one, and it should have
lapsed before this one starts. See
[phase 00](../docs/phases/00-data-access-and-the-clock.md).
Finish the pipeline, let it run for two weeks so you have real history, then install
the connector and spend all 14 days on the dashboard.

**The workbook is the one manual step left.** The pipeline was built in an
environment without Tableau, so everything up to and including the extracts is done
and exercised, and the views below are specified against those extracts rather than
built. Building them is an afternoon in Tableau Public against `extracts/*.csv`.

## Contents

```
tableau/
+-- usl_attendance.twb     The workbook, once built. Commit it - it is XML, it diffs
+-- extracts/              CSV output of python -m usl.run export. Gitignored, regenerable
```

## The extracts

`python -m usl.run export` writes one CSV per table plus one joined file:

| File | Grain | Feeds |
|---|---|---|
| `mart_match_features.csv` | One row per match, features and target | Views 1 and 2 |
| `predictions.csv` | One row per match per model per run | View 1, view 3 |
| `predictions_with_band.csv` | `predictions` joined to each run's MAE: `band_low`, `band_high`, `band_label` | View 3 |
| `model_metrics.csv` | One row per model per run: MAE, MAPE, RMSE, sizes | Tracker strip |
| `model_variance.csv` | MAE per model per seed per run | Tracker strip, the noise floor |
| `model_cv.csv` | Expanding-window folds by season (empty with one season) | Tracker strip |
| `feature_importance.csv` | Gain per feature per model per run, `is_prorel` | Tracker strip |
| `int_standings.csv` | Full-field conference table as of every match date, plus the season-end snapshot (`is_match_date = false`) | View 2 |
| `int_stakes.csv` | Playoff and relegation lines, `is_mathematically_live`, `eliminated_on` | View 2 |
| `mart_decay_curve.csv` | Indexed attendance by `matches_since_elimination`, with `n` | View 2 |
| `stg_clubs.csv` | `club_id` to `display_name` and conference per season | Every view, for labels |
| `run_log.csv`, `check_log.csv` | Every run and every check | The "last updated" tile |

Join `home_club_id` to `stg_clubs.club_id` (and `season`) for display names.

## The three views

1. **League overview** - actual versus predicted attendance by club, from
   `predictions.csv` filtered to `model_name = 'prorel'` (or a parameter to toggle
   `baseline`), one bar or dot per club with the club's actual alongside. Credibility
   first.
2. **Pro-rel view** - `rank_before` against `attendance` from the mart, with a trend
   line, **labelled on the view itself** as exploratory: correlation with league
   position, not a measured relegation effect. Beside it, `mart_decay_curve.csv`:
   `index_vs_own_baseline` by `matches_since_elimination`, with `n` shown on every
   point. That curve is measured, not projected.
3. **Club drill-down** - one club, its season to date from the mart, and forecasts for
   remaining home matches from `predictions_with_band.csv` with `band_low` to
   `band_high` shaded and `band_label` as the caption (it is plus or minus the
   holdout MAE, historical residuals, and the label says so).

Plus a **tracker strip** below the fold: `feature_importance.csv` as a bar chart
coloured by `is_prorel`, and `model_metrics.csv` MAE over `run_date` by
`model_name`, with the `model_variance.csv` spread as the band the A-to-B gap has to
clear.

## After the trial expires

The software locks. The data is untouched and the `.twb` is intact, but you cannot
open it. Rebuild against `extracts/*.csv` in Tableau Public. This is why
`usl/export/extracts.py` was written before the trial starts, not after - and why
you record the video during the trial, while the live connection still works.
