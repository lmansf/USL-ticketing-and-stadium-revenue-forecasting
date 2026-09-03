# Phase 07 - Two models

**Goal.** One dataset, two feature lists, three output tables, and an answer to the
headline question that is attributable to the pro-rel features and nothing else.

**MVP cut.** Same two models, default hyperparameters, single chronological holdout.
See [docs/mvp/03-mvp-models.md](../mvp/03-mvp-models.md).

**Files.** `usl/models/train.py`, `usl/models/metrics.py`, `usl/features/definitions.py`

---

## The design

- **Model A, `baseline`:** calendar, lags, match context. Blind to the table.
- **Model B, `prorel`:** everything in A, plus the pro-rel family.

They read the same `mart_match_features`. **The split happens in code, by column
selection.** No second mart, no duplicated tables, no separate pipeline. This is the
part of the design that makes the comparison mean anything: if the two models drew
from different tables, any difference between them could be a difference in the data
rather than in the features, and you would have no way to tell which.

The only new objects are outputs:

```sql
CREATE TABLE predictions (
    match_id     VARCHAR,
    model_name   VARCHAR,   -- 'baseline' | 'prorel'
    run_date     DATE,
    predicted    DOUBLE,
    actual       DOUBLE,    -- null until played
    PRIMARY KEY (match_id, model_name, run_date)
);

CREATE TABLE model_metrics (
    model_name   VARCHAR,
    run_date     DATE,
    mae          DOUBLE,
    mape         DOUBLE,
    rmse         DOUBLE,
    n_train      INTEGER,
    n_test       INTEGER,
    PRIMARY KEY (model_name, run_date)
);

CREATE TABLE feature_importance (
    model_name   VARCHAR,
    run_date     DATE,
    feature      VARCHAR,
    importance   DOUBLE,
    is_prorel    BOOLEAN,
    PRIMARY KEY (model_name, run_date, feature)
);
```

Keyed by `model_name` and `run_date`, so Tableau gets a filter that toggles models or
overlays both, and everything is a time series from the first run. Do not overwrite
these tables each week. The accumulating history is the asset - week four is when they
start being interesting, and you cannot backfill a decision you did not log.

---

## Validation

**Chronological split, not random.** Predicting attendance for a match when you
trained on the following week's data is leakage, and `train_test_split` with a random
seed does exactly that. Hold out the most recent N matches, or use expanding-window CV
by season.

**Log a naive baseline alongside both models.** The club's mean home attendance this
season. If XGBoost cannot beat that, you want to be the one who noticed.

The naive baseline is not a formality. Attendance is dominated by which club is at
home, that is mostly stable within a season, and a club-mean predictor is genuinely
hard to beat. A model that beats it by a wide margin usually has leakage rather than
skill, and checking that first will save you from presenting one.

---

## Exercise 7.1 - Importance and error, together

Feature importance says a feature was *used*. Error says it *helped*. Log both per run
so the dashboard can show either, and so the headline question has two independent
lines of evidence rather than one.

<details>
<summary>Solution</summary>

```python
for name, features in [("baseline", BASE_FEATURES),
                       ("prorel", BASE_FEATURES + PROREL_FEATURES)]:
    model = train(X[features], y)
    preds = model.predict(X_test[features])

    metrics_rows.append({
        "model_name": name, "run_date": today,
        "mae": mean_absolute_error(y_test, preds),
        "mape": mean_absolute_percentage_error(y_test, preds),
        "rmse": root_mean_squared_error(y_test, preds),
        "n_train": len(X), "n_test": len(X_test),
    })

    imp = model.get_booster().get_score(importance_type="gain")
    for feat, score in imp.items():
        importance_rows.append({
            "model_name": name, "run_date": today,
            "feature": feat, "importance": score,
            "is_prorel": feat in PROREL_FEATURES,
        })
```

Use `gain`, not `weight` - weight counts splits, which flatters high-cardinality
features. If you want the stronger version, permutation importance on the holdout
measures actual delta-MAE, which is what you really mean by "impact". Gain is cheap
enough to log every week; permutation is worth running monthly.

One trap: `get_score` omits features the model never split on, so a feature with zero
importance is *missing* from the dict rather than present with a zero. Reindex against
the full feature list before writing, or the dashboard will show a gap where it should
show a zero - and "the model ignored this feature entirely" is one of the more
interesting things the chart can tell you about `points_from_relegation_line`.

`is_prorel` is what drives the colour split in the Tableau bar chart.
</details>

---

## Exercise 7.2 - Reading the comparison honestly

Model B beats Model A by 40 attendees of MAE on a holdout of 60 matches. Is that the
answer to the headline question?

<details>
<summary>Solution</summary>

No, not on its own. Three things have to be true before that number means what it
looks like it means.

**It has to be bigger than run-to-run variance.** XGBoost with a different seed moves
MAE around. Train both models across several seeds, or several expanding-window folds,
and compare the distributions rather than two point estimates. If the gap is inside
the spread of Model A against itself, there is no finding.

**It has to survive the naive baseline.** If both models sit near the club-mean
predictor, a 40-attendee gap between them is two variations on "not much".

**It has to be consistent in sign.** One good week is noise. This is the argument for
accumulating `model_metrics` weekly from the first run: after two months you can plot
the gap over time, and a gap that is consistently positive is a far stronger claim
than a single holdout, because you did not choose the holdout.

The honest write-up of a real result looks like: "over N weekly runs, the pro-rel model
had lower MAE in M of them, median improvement X attendees, against a run-to-run
standard deviation of Y." That is a sentence you can defend. "Model B is better" is
not.

And if the gap is zero, say so. A well-instrumented null result on the headline
question is a real finding, and it is the honest input to USL's 2028 planning.
</details>

---

## Handling nulls

XGBoost handles nulls natively - it learns a default direction at each split. That is a
legitimate choice, and so is imputing, and so is failing the run. What is not
legitimate is not knowing which one you did.

Decide, encode it in `features_not_null` in `usl/transform/checks.py`, and be ready to
explain the choice. This is demo scenario D4 in [phase 09](09-break-and-fix.md), and
the scenario is explicitly about explaining the choice rather than about the null
being a bug.

---

## What "done" looks like

- `python -m usl.run train` writes rows to all three output tables for both models.
- Running it a second time on the same date updates rather than duplicates.
- The naive baseline appears in `model_metrics` alongside the two models.
- Feature importance includes zero-importance features as zeros, not as absences.
- `tests/test_models.py` passes.

Next: [phase 08 - Tableau](08-tableau.md).
