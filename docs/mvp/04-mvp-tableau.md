# MVP 04 - Tableau Public via CSV

**Goal.** A dashboard in the free edition, with no trial clock running.

**Extends into:** [phase 08](../phases/08-tableau.md),
[reference/tableau-duckdb-connector.md](../reference/tableau-duckdb-connector.md)

---

## Skip the connector

Tableau Public is free and connects to *files only* - not DuckDB, not any database. The
DuckDB JDBC connector needs Tableau Desktop, which is a 14-day trial.

**Do not start that trial yet.** It is the only part of this project on a clock, and
you want all 14 days for the dashboard rather than spending three of them discovering
your mart has a bug. The full track starts the trial at step 9 of the build order, after
everything else is finished and data has been flowing for two weeks.

So: export CSVs, connect Tableau Public to those.

---

## Export

```python
TABLES = ["mart_match_features", "predictions", "model_metrics", "feature_importance"]

for t in TABLES:
    con.sql(f"SELECT * FROM {t}").df().to_csv(f"tableau/extracts/{t}.csv", index=False)
```

`python -m usl.run export`. Extracts are gitignored; the code that writes them is not.

This step is not throwaway. It stays in the full track as the fallback export path -
it is what your workbook falls back to when the Desktop trial expires on day 15, and it
is what makes this repo useful to someone who has no Tableau at all.

---

## Two views

The full track has three views plus a tracker strip. The MVP has two, and they are the
first two beats of the same story.

**1. Actual versus predicted attendance by club.** Credibility first. If this view does
not look right, nothing after it matters.

**2. Rank against attendance, with the relationship fitted.** The mechanism. Label it
as exploratory *on the view itself*, not only in a caption: no relegation has occurred
in this data, so this is correlation with league position, not a measured relegation
effect.

Skip the club drill-down and the tracker strip for now. The drill-down needs an
uncertainty band the MVP does not compute, and the tracker needs weeks of accumulated
`model_metrics` rows that do not exist yet on day one.

---

## Exercise M4.1 - The refresh problem

Tableau Public connected to a CSV does not know the CSV changed. What happens on
Tuesday when the pipeline rewrites the file, and what do you do about it?

<details>
<summary>Solution</summary>

Nothing happens. Tableau holds an extract of the file as it was when you connected, and
a published Tableau Public workbook has no path to a file on your laptop at all - the
data was uploaded with it.

So there are two different problems wearing one hat.

**Locally**, refresh the data source after a run. Manual, and easy to forget, which is
the honest weakness of the MVP visualisation path.

**Published**, the workbook is a snapshot. Updating it means re-uploading. For a
portfolio piece that is fine - you publish the version you want people to see, and it
does not go stale in a way that misleads anyone, because it is dated.

What this cannot do is be an operational dashboard someone checks on Tuesday morning.
That needs the live connection, which needs Desktop. Being able to state that
distinction precisely is worth more in an interview than having built the live version,
because it is a licensing constraint rather than a technical one and pretending
otherwise is transparent.
</details>

---

## Done when

- `python -m usl.run export` writes CSVs to `tableau/extracts/`.
- Both views render in Tableau Public from those files.
- The rank view carries its exploratory label on the view.

Next: [MVP 05 - Schedule it](05-mvp-schedule.md).
