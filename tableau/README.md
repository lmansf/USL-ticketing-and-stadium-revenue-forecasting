# Tableau

Two connection paths, and which one you use depends on which edition you have
running.

| | Tableau Public (free) | Tableau Desktop (14-day trial) |
|---|---|---|
| Connects to | Files only | Files and databases |
| DuckDB | No. The connector will not appear | Yes, via the JDBC connector and `.taco` |
| Use it for | The shipped artifact, and after day 15 | Building the live dashboard, and the video |
| Doc | [MVP 04](../docs/mvp/04-mvp-tableau.md) | [phase 08](../docs/phases/08-tableau.md), [connector setup](../docs/reference/tableau-duckdb-connector.md) |

**Start the trial last.** It is the only clock in this project. Finish the
pipeline, let it run for two weeks so you have real history, then install the
connector and spend all 14 days on the dashboard.

## Contents

```
tableau/
+-- usl_attendance.twb     The workbook. Commit it - it is XML, it diffs
+-- extracts/              CSV or Hyper output. Gitignored, regenerable
```

Regenerate extracts with `python -m usl.run export`. The code that writes them is
committed; the files themselves are not.

## The three views

1. **League overview** - actual versus predicted attendance by club. Credibility
   first.
2. **Pro-rel view** - table position against attendance, fitted, plus the
   dead-rubber decay curve. Label it exploratory *on the view*.
3. **Club drill-down** - one club, season to date, forecasts with an uncertainty
   band.

Plus a **tracker strip** below the fold: feature importance with pro-rel features
in a contrasting colour, and MAE over time by model.

## After the trial expires

The software locks. The data is untouched and the `.twb` is intact, but you cannot
open it. Rebuild against `extracts/*.csv` in Tableau Public. This is why
`usl/export/extracts.py` gets written before the trial starts, not after - and why
you record the video during the trial, while the live connection still works.
