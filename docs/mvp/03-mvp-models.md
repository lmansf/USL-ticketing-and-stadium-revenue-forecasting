# MVP 03 - Both models

**Goal.** Two XGBoost models, default hyperparameters, one chronological holdout, MAE
and feature importance written to tables.

**Extends into:** [phase 07](../phases/07-two-models.md)

---

## Both, not one

The headline question is the comparison. One model is not a smaller version of this
project - it is a different project that cannot answer the question the project exists
to ask.

```python
BASE_FEATURES   = ["day_of_week", "month", "is_weekend", "is_midweek",
                   "last_home_gate", "home_gate_ma3", "opponent_club_id"]
PROREL_FEATURES = ["rank_before", "opponent_rank_before", "rank_gap"]

MODELS = {
    "baseline": BASE_FEATURES,
    "prorel":   BASE_FEATURES + PROREL_FEATURES,
}
```

Same dataframe, two column selections. No second mart, no second query. If the two
models read from different tables, any difference between them could be a difference in
the data rather than in the features, and you lose the ability to attribute the result.

---

## Chronological split

Not `train_test_split`. Not a random seed.

```python
df = df.sort_values("date")
cutoff = int(len(df) * 0.8)
train, test = df.iloc[:cutoff], df.iloc[cutoff:]
```

Predicting attendance for a match while trained on the following week's data is
leakage, and a random split does exactly that. With one season this is a single
holdout of the last few matchdays; the full track uses expanding-window CV by season.

---

## The naive baseline

Log it alongside both models, every run:

```python
naive = train.groupby("home_club_id")["attendance"].mean()
naive_preds = test["home_club_id"].map(naive)
```

The club's mean home attendance. Attendance is dominated by which club is at home, that
is mostly stable within a season, and this predictor is genuinely hard to beat. If
XGBoost cannot beat it, you want to be the one who noticed. If XGBoost beats it by a
huge margin, suspect leakage before you celebrate.

Write it into `model_metrics` as a third `model_name`, so the comparison is in the
table rather than in your head.

---

## Default hyperparameters

`XGBRegressor()` with defaults. Tuning is not where the value is here, and a tuned
model on one season of a few hundred matches is mostly tuned to that holdout.

The one parameter worth setting is `random_state`, so a re-run is reproducible.

---

## Output tables

The MVP writes the same three tables as the full track, with the same keys:

```sql
predictions        (match_id, model_name, run_date, predicted, actual)
model_metrics      (model_name, run_date, mae, mape, rmse, n_train, n_test)
feature_importance (model_name, run_date, feature, importance, is_prorel)
```

Do not skip `run_date`, and do not overwrite these each run. The accumulating history
is the asset - week four is when they get interesting, and you cannot backfill a
measurement you did not take. This costs nothing now and is annoying to retrofit.

Use `importance_type="gain"`, not `weight`. Weight counts splits, which flatters
high-cardinality features - `opponent_club_id` would top the chart on split count alone
and tell you nothing.

---

## Exercise M3.1 - Read the result

Model B has lower MAE than Model A. What do you have to check before saying league
position predicts attendance?

<details>
<summary>Solution</summary>

At minimum, that the gap is bigger than the noise. Re-run both models with three or
four different `random_state` values and look at the spread of Model A against itself.
If the A-to-B gap sits inside that spread, there is no finding yet - and with a
single-season MVP holdout of maybe fifty matches, it very often will.

Also check the naive baseline. If both models are close to the club-mean predictor,
then the difference between them is a difference between two things that are not doing
much.

The honest MVP conclusion is usually "the pipeline works, the comparison is set up
correctly, and the sample is too small to answer the question yet". That is the right
answer, and it is exactly why the first graduation step is backfilling the other eight
seasons.
</details>

---

## Done when

- All three tables have rows for `baseline`, `prorel`, and the naive baseline.
- Re-running on the same date updates rather than duplicates.
- You can state the MAE gap and whether it exceeds run-to-run variance.

Next: [MVP 04 - Tableau Public](04-mvp-tableau.md).
