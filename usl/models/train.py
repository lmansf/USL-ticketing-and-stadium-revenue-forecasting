"""Train both models and write the output tables.

One dataset, two feature lists, no duplicated tables. The split happens in code,
by column selection - see usl/features/definitions.py. Anything that differed
between the two models other than the column list would make a difference in
error unattributable, so the same frame, the same split, the same seed and the
same training call are used for both.

Five output tables, keyed by model_name and run_date so Tableau gets a filter
that toggles models or overlays both, and so everything is a time series from
the first run:

  predictions        one row per match per model per run (holdout and forecasts)
  model_metrics      the primary numbers: one chronological holdout
  feature_importance gain per feature, zeros included
  model_cv           expanding-window folds by season (empty with one season)
  model_variance     holdout MAE per seed, the noise floor the A-to-B gap has
                     to clear (exercise 7.2)

Every write is an upsert on the primary key, so a rerun on the same run_date
updates rather than duplicates and a run on a new date accumulates. Do NOT
overwrite these tables each week. The history is the asset - week four is when
it starts being interesting, and you cannot backfill a measurement you did not
take.

See docs/phases/07-two-models.md
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Any

import duckdb
import pandas as pd
import xgboost as xgb

from usl import config
from usl.features.definitions import MODEL_FEATURES, is_prorel
from usl.models.metrics import ErrorMetrics, compute_metrics, naive_club_mean

log = logging.getLogger(__name__)

PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS predictions (
    match_id   VARCHAR,
    model_name VARCHAR,   -- 'baseline' | 'prorel' | 'naive_club_mean'
    run_date   DATE,
    predicted  DOUBLE,
    actual     DOUBLE,    -- null until played
    PRIMARY KEY (match_id, model_name, run_date)
);
"""

MODEL_METRICS_DDL = """
CREATE TABLE IF NOT EXISTS model_metrics (
    model_name VARCHAR,
    run_date   DATE,
    mae        DOUBLE,
    mape       DOUBLE,
    rmse       DOUBLE,
    n_train    INTEGER,
    n_test     INTEGER,
    PRIMARY KEY (model_name, run_date)
);
"""

FEATURE_IMPORTANCE_DDL = """
CREATE TABLE IF NOT EXISTS feature_importance (
    model_name VARCHAR,
    run_date   DATE,
    feature    VARCHAR,
    importance DOUBLE,
    is_prorel  BOOLEAN,
    PRIMARY KEY (model_name, run_date, feature)
);
"""

# Expanding-window cross-validation by season: for each season after the
# first, train on every earlier season and test on that one. Empty with a
# single season, which is the honest state of the example data.
MODEL_CV_DDL = """
CREATE TABLE IF NOT EXISTS model_cv (
    model_name  VARCHAR,
    run_date    DATE,
    fold_season INTEGER,  -- the season held out; training is every season before it
    mae         DOUBLE,
    mape        DOUBLE,
    rmse        DOUBLE,
    n_train     INTEGER,
    n_test      INTEGER,
    PRIMARY KEY (model_name, run_date, fold_season)
);
"""

# Holdout MAE per seed. The first seed's numbers are the ones in model_metrics;
# the spread across all of them is the run-to-run noise. If the A-to-B gap sits
# inside the spread of A against itself, there is no finding.
MODEL_VARIANCE_DDL = """
CREATE TABLE IF NOT EXISTS model_variance (
    model_name VARCHAR,
    run_date   DATE,
    seed       INTEGER,
    mae        DOUBLE,
    PRIMARY KEY (model_name, run_date, seed)
);
"""

NAIVE_MODEL_NAME = "naive_club_mean"

# Features passed to XGBoost as pandas categoricals rather than numbers. The
# category set is fixed to every club in the mart before any split, so the
# integer codes cannot drift between train, holdout and forecast rows.
CATEGORICAL_FEATURES: tuple[str, ...] = ("opponent_club_id",)

# Columns written to each output table, in table order, with the SQL type each
# is cast to on the way in. Frames are registered and inserted with SELECT so
# the types are DuckDB's decision, not pandas'.
_TABLE_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "predictions": (
        ("match_id", "VARCHAR"),
        ("model_name", "VARCHAR"),
        ("run_date", "DATE"),
        ("predicted", "DOUBLE"),
        ("actual", "DOUBLE"),
    ),
    "model_metrics": (
        ("model_name", "VARCHAR"),
        ("run_date", "DATE"),
        ("mae", "DOUBLE"),
        ("mape", "DOUBLE"),
        ("rmse", "DOUBLE"),
        ("n_train", "INTEGER"),
        ("n_test", "INTEGER"),
    ),
    "feature_importance": (
        ("model_name", "VARCHAR"),
        ("run_date", "DATE"),
        ("feature", "VARCHAR"),
        ("importance", "DOUBLE"),
        ("is_prorel", "BOOLEAN"),
    ),
    "model_cv": (
        ("model_name", "VARCHAR"),
        ("run_date", "DATE"),
        ("fold_season", "INTEGER"),
        ("mae", "DOUBLE"),
        ("mape", "DOUBLE"),
        ("rmse", "DOUBLE"),
        ("n_train", "INTEGER"),
        ("n_test", "INTEGER"),
    ),
    "model_variance": (
        ("model_name", "VARCHAR"),
        ("run_date", "DATE"),
        ("seed", "INTEGER"),
        ("mae", "DOUBLE"),
    ),
}

_TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "predictions": ("match_id", "model_name", "run_date"),
    "model_metrics": ("model_name", "run_date"),
    "feature_importance": ("model_name", "run_date", "feature"),
    "model_cv": ("model_name", "run_date", "fold_season"),
    "model_variance": ("model_name", "run_date", "seed"),
}

_TOP_FEATURES = 10


def ensure_output_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create the five output tables if absent.

    predictions, model_metrics, feature_importance, model_cv, model_variance.
    CREATE TABLE IF NOT EXISTS, never CREATE OR REPLACE: the accumulated history
    in these tables is the point of them.

    Args:
        con: Open connection with write access.
    """
    for ddl in (
        PREDICTIONS_DDL,
        MODEL_METRICS_DDL,
        FEATURE_IMPORTANCE_DDL,
        MODEL_CV_DDL,
        MODEL_VARIANCE_DDL,
    ):
        con.execute(ddl)


def load_training_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Read mart_match_features, applying the COVID exclusion.

    2020 attendance is not demand signal. config.DROP_COVID is read here, at
    call time, and defaults to on; flipping it is how you show the difference.

    Unplayed fixtures are kept - attendance null, is_played false - because the
    forecasts for remaining home matches need their features. Callers split on
    those two columns; nothing here does.

    Args:
        con: Open connection.

    Returns:
        One row per match, sorted by date then match_id, index reset.
    """
    frame = con.execute("SELECT * FROM mart_match_features").df()
    n_read = len(frame)
    if config.DROP_COVID:
        covid = frame["is_covid_affected"].astype("boolean").fillna(False).astype(bool)
        frame = frame.loc[~covid]
        log.info(
            "training frame: %d rows read, %d COVID-affected rows dropped "
            "(config.DROP_COVID, window %s to %s)",
            n_read,
            int(covid.sum()),
            config.COVID_START,
            config.COVID_END,
        )
    else:
        log.info("training frame: %d rows read, COVID rows kept (config.DROP_COVID is off)", n_read)
    return frame.sort_values(["date", "match_id"], kind="stable").reset_index(drop=True)


def categorical_levels(df: pd.DataFrame) -> dict[str, list[str]]:
    """The full category set of every categorical feature present in a frame.

    Call it on the whole mart, before any split, and pass the result to
    prepare_features for every subset. That is what keeps opponent_club_id's
    integer codes identical across train, holdout and forecast rows - a club
    that only appears in the holdout would otherwise get a code the model has
    never seen under a different meaning.

    Args:
        df: The frame to take levels from, normally the full training frame.

    Returns:
        Feature name to sorted list of its distinct non-null values, as strings.
    """
    levels: dict[str, list[str]] = {}
    for feature in CATEGORICAL_FEATURES:
        if feature in df.columns:
            values = df[feature].dropna()
            levels[feature] = sorted({str(v) for v in values})
    return levels


def prepare_features(
    df: pd.DataFrame,
    features: Sequence[str],
    *,
    categories: dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """Build the model matrix: the selected columns, typed for XGBoost.

    Booleans become integers, categoricals become pandas Categoricals with a
    fixed category set, and everything else becomes float. Nulls stay as NaN -
    XGBoost learns a default direction for them. The null policy itself lives in
    config.ALLOWED_NULL_FEATURES and is enforced upstream by features_not_null;
    this function does not impute and does not check.

    Args:
        df: Rows to build the matrix for. Any subset of the mart.
        features: The column selection defining the model.
        categories: Category set per categorical feature, from
            categorical_levels on the FULL frame. When omitted the levels are
            taken from df itself, which is only correct when df is the full
            frame; a value outside the given categories becomes NaN, which the
            model treats as missing.

    Returns:
        A frame with exactly the requested columns, in order, on df's index.
    """
    levels = categories if categories is not None else categorical_levels(df)
    matrix = pd.DataFrame(index=df.index)
    for feature in features:
        column = df[feature]
        if feature in levels:
            as_text = column.astype("string").astype(object)
            matrix[feature] = pd.Categorical(as_text, categories=levels[feature])
        elif _is_boolean(column):
            flags = column.astype("boolean")
            if flags.isna().any():
                # A null boolean is outside the allowed set and the check would
                # have failed the run; keep the NaN rather than invent a value.
                matrix[feature] = flags.astype("float64")
            else:
                matrix[feature] = flags.astype("int64")
        else:
            matrix[feature] = pd.to_numeric(column, errors="raise").astype("float64")
    return matrix


def _is_boolean(column: pd.Series) -> bool:
    """Whether a column holds booleans, including the nullable extension dtype."""
    if pd.api.types.is_bool_dtype(column):
        return True
    if column.dtype == object:
        values = column.dropna()
        return not values.empty and all(isinstance(v, bool) for v in values)
    return False


def chronological_split(
    df: pd.DataFrame, test_fraction: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by date, not at random.

    Predicting attendance for a match while trained on the following week's data
    is leakage, and train_test_split with a random seed does exactly that. The
    holdout is the most recent round(len * test_fraction) rows.

    The sort is stable, so rows already ordered by (date, match_id) keep that
    order, and the original index values are preserved so predictions can be
    aligned back to the frame they came from.

    Args:
        df: Feature frame with a date column.
        test_fraction: Proportion held out, from the end. 0 <= f < 1.

    Returns:
        (train, test).

    Raises:
        ValueError: test_fraction outside [0, 1).
    """
    if not 0 <= test_fraction < 1:
        raise ValueError(f"test_fraction must be in [0, 1), got {test_fraction}")
    ordered = df.sort_values("date", kind="stable")
    n_test = int(round(len(ordered) * test_fraction))
    cutoff = len(ordered) - n_test
    return ordered.iloc[:cutoff], ordered.iloc[cutoff:]


def season_folds(df: pd.DataFrame) -> list[tuple[int, pd.DataFrame, pd.DataFrame]]:
    """Expanding-window folds by season.

    For every season after the first: train on every earlier season, test on
    that one. Nine seasons give eight folds; the example season gives none,
    which is why model_cv can legitimately be empty.

    Args:
        df: Frame with a season column.

    Returns:
        (fold_season, train, test) per fold, in season order. Empty with one
        season or none.
    """
    seasons = sorted(int(s) for s in df["season"].dropna().unique())
    folds: list[tuple[int, pd.DataFrame, pd.DataFrame]] = []
    for season in seasons[1:]:
        train = df.loc[df["season"] < season]
        test = df.loc[df["season"] == season]
        folds.append((season, train, test))
    return folds


def train_one(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: Sequence[str],
    *,
    seed: int | None = None,
    categories: dict[str, list[str]] | None = None,
) -> tuple[xgb.XGBRegressor, pd.Series]:
    """Fit one XGBoost model and predict the given rows.

    config.XGB_PARAMS is read here, at call time, so a test or an experiment can
    change it without re-importing. enable_categorical and the hist tree method
    are set on top of it: categorical support needs hist.

    Args:
        train: Training rows.
        test: Rows to predict - the holdout, or unplayed fixtures.
        features: The column selection defining this model.
        seed: Overrides random_state from config.XGB_PARAMS when given. The
            variance estimate refits with several of these.
        categories: Category levels from categorical_levels on the full frame.
            Defaults to the levels seen across train and test together, which
            is enough for the codes to agree between the two.

    Returns:
        (fitted model, predictions aligned to test's index, float64).
    """
    params: dict[str, Any] = dict(config.XGB_PARAMS)
    if seed is not None:
        params["random_state"] = seed
    params["enable_categorical"] = True
    params["tree_method"] = "hist"

    levels = categories
    if levels is None:
        present = [f for f in CATEGORICAL_FEATURES if f in features]
        levels = categorical_levels(pd.concat([train[present], test[present]]))

    x_train = prepare_features(train, features, categories=levels)
    y_train = pd.to_numeric(train[config.TARGET], errors="raise").astype("float64")
    model = xgb.XGBRegressor(**params)
    model.fit(x_train, y_train)

    x_test = prepare_features(test, features, categories=levels)
    predicted = model.predict(x_test).astype("float64") if len(x_test) else []
    return model, pd.Series(predicted, index=test.index, name="predicted", dtype="float64")


def extract_importance(model: xgb.XGBRegressor, features: Sequence[str]) -> pd.DataFrame:
    """Pull feature importance from a fitted booster, zeros included.

    Gain, not weight (config.IMPORTANCE_TYPE). Weight counts splits, which
    flatters high-cardinality features - opponent_club_id would top the chart on
    split count alone and tell you nothing.

    get_score omits features the model never split on, so a zero-importance
    feature is MISSING from its dict rather than present with a zero. The result
    is reindexed over the full feature list, or the dashboard shows a gap where
    it should show a zero - and "the model ignored this feature entirely" is one
    of the more interesting things the chart can say about
    points_from_relegation_line.

    Args:
        model: Fitted XGBRegressor.
        features: The full feature list for this model, for reindexing.

    Returns:
        Columns feature, importance, is_prorel. One row per feature, in the
        order given, zeros for features the model never used.
    """
    scores = model.get_booster().get_score(importance_type=config.IMPORTANCE_TYPE)
    rows = [
        {
            "feature": feature,
            "importance": _scalar_score(scores.get(feature, 0.0)),
            "is_prorel": is_prorel(feature),
        }
        for feature in features
    ]
    return pd.DataFrame(rows, columns=["feature", "importance", "is_prorel"])


def _scalar_score(value: float | list[float]) -> float:
    """One number per feature.

    A single-output regressor scores each feature with a float. XGBoost types
    the dict as float-or-list because a multi-output booster returns one score
    per output; this pipeline never builds one, and if it ever did the first
    output's score is the only defensible single number to chart.
    """
    if isinstance(value, list):
        return float(value[0]) if value else 0.0
    return float(value)


def train_all(
    con: duckdb.DuckDBPyConnection,
    run_date: dt.date | None = None,
    *,
    seeds: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Train every model plus the naive baseline and write all five tables.

    Feature importance says a feature was used. Error says it helped. Both are
    written per run so the dashboard can show either, and so the headline
    question has two independent lines of evidence rather than one.

    The sequence:

    1. Load the mart, drop COVID rows, split played rows from unplayed.
    2. One chronological holdout of the played rows (config.TEST_FRACTION).
    3. Fit baseline and prorel on the training rows with the first seed, score
       the holdout: model_metrics, predictions, feature_importance.
    4. The naive club-mean baseline on the same split: model_metrics and
       predictions under NAIVE_MODEL_NAME. If XGBoost cannot beat it, you want
       to be the one who noticed.
    5. Refit both XGBoost models on every played row and forecast the unplayed
       fixtures, written to predictions with actual null. Skipped when there
       are none.
    6. Every seed in turn on the same split: model_variance. The first seed is
       the one whose numbers are in model_metrics, so its row is that MAE.
    7. Expanding-window folds by season, all three model names: model_cv.

    Every write is an upsert on the table's primary key, so re-running on the
    same run_date updates rather than duplicates.

    Args:
        con: Open connection with write access.
        run_date: Defaults to today. Explicit for testing and for backfilling a
            missed run.
        seeds: Seeds for the variance estimate. Defaults to
            config.VARIANCE_SEEDS; the first is the primary seed.

    Returns:
        A summary for the run log and the console: sizes, the split date, MAE
        per model, top features by gain, the variance spread and the CV folds.

    Raises:
        ValueError: No played rows, or a split that leaves either side empty.
    """
    run_date = run_date or dt.date.today()
    seed_list = tuple(int(s) for s in (seeds if seeds is not None else config.VARIANCE_SEEDS))
    if not seed_list:
        raise ValueError("at least one seed is required (config.VARIANCE_SEEDS is empty)")
    primary_seed = seed_list[0]

    ensure_output_tables(con)
    frame = load_training_frame(con)
    levels = categorical_levels(frame)

    played_flag = frame["is_played"].astype("boolean").fillna(False).astype(bool)
    has_gate = frame[config.TARGET].notna()
    # Train on played matches with a recorded gate; forecast only fixtures not
    # yet played. A played match with no recorded gate is neither - forecasting
    # it would list a match that already happened as a remaining fixture.
    played = frame.loc[played_flag & has_gate]
    future = frame.loc[~played_flag]
    no_gate = int((played_flag & ~has_gate).sum())
    if no_gate:
        log.info(
            "%d played match(es) with no recorded gate: excluded from training, not forecast",
            no_gate,
        )
    if played.empty:
        raise ValueError("mart_match_features has no played rows with attendance; nothing to train")

    train, test = chronological_split(played, config.TEST_FRACTION)
    if train.empty or test.empty:
        raise ValueError(
            f"chronological split of {len(played)} played rows at TEST_FRACTION "
            f"{config.TEST_FRACTION} leaves train={len(train)} test={len(test)}; "
            "both sides need rows"
        )
    split_date = _as_date(test["date"].min())
    y_test = pd.to_numeric(test[config.TARGET], errors="raise").astype("float64")
    log.info(
        "train: run_date=%s played=%d train=%d test=%d split_date=%s future=%d seeds=%s",
        run_date,
        len(played),
        len(train),
        len(test),
        split_date,
        len(future),
        seed_list,
    )

    metrics_rows: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    importance_rows: list[dict[str, Any]] = []
    variance_rows: list[dict[str, Any]] = []
    cv_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "run_date": run_date,
        "rows_read": int(len(frame)),
        "n_played": int(len(played)),
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "n_future": int(len(future)),
        "n_no_gate": no_gate,
        "split_date": split_date,
        "seeds": seed_list,
        "feature_count": {},
        "mae": {},
        "metrics": {},
        "top_features": {},
        "variance": {},
        "cv": {},
    }

    # 3. the two models on the holdout
    for name, features in MODEL_FEATURES.items():
        summary["feature_count"][name] = len(features)
        log.info("model %s: %d features", name, len(features))
        model, predicted = train_one(train, test, features, seed=primary_seed, categories=levels)
        metrics = compute_metrics(y_test, predicted, n_train=len(train))
        _log_metrics(name, metrics)
        metrics_rows.append(_metrics_row(name, run_date, metrics))
        prediction_rows.extend(_prediction_rows(test, name, run_date, predicted, y_test))
        variance_rows.append(
            {"model_name": name, "run_date": run_date, "seed": primary_seed, "mae": metrics.mae}
        )

        importance = extract_importance(model, features)
        for feature, score, flag in zip(
            importance["feature"].tolist(),
            importance["importance"].tolist(),
            importance["is_prorel"].tolist(),
            strict=True,
        ):
            importance_rows.append(
                {
                    "model_name": name,
                    "run_date": run_date,
                    "feature": str(feature),
                    "importance": float(score),
                    "is_prorel": bool(flag),
                }
            )
        top = importance.sort_values("importance", ascending=False, kind="stable").head(
            _TOP_FEATURES
        )
        top_pairs = [
            (str(feature), float(score))
            for feature, score in zip(
                top["feature"].tolist(), top["importance"].tolist(), strict=True
            )
        ]
        log.info(
            "model %s: top %d features by %s: %s",
            name,
            len(top_pairs),
            config.IMPORTANCE_TYPE,
            ", ".join(f"{f}={v:.1f}" for f, v in top_pairs),
        )
        summary["mae"][name] = metrics.mae
        summary["metrics"][name] = metrics
        summary["top_features"][name] = top_pairs
        summary["variance"][name] = {primary_seed: metrics.mae}

    # 4. the naive baseline on the same split
    naive_predicted = naive_club_mean(train, test)
    naive_metrics = compute_metrics(y_test, naive_predicted, n_train=len(train))
    _log_metrics(NAIVE_MODEL_NAME, naive_metrics)
    metrics_rows.append(_metrics_row(NAIVE_MODEL_NAME, run_date, naive_metrics))
    prediction_rows.extend(
        _prediction_rows(test, NAIVE_MODEL_NAME, run_date, naive_predicted, y_test)
    )
    summary["mae"][NAIVE_MODEL_NAME] = naive_metrics.mae
    summary["metrics"][NAIVE_MODEL_NAME] = naive_metrics
    for name in MODEL_FEATURES:
        log.info(
            "model %s against %s: MAE gap %+.1f (negative is better than the club mean)",
            name,
            NAIVE_MODEL_NAME,
            summary["mae"][name] - naive_metrics.mae,
        )

    # 5. forecasts for unplayed fixtures, refit on every played row
    if future.empty:
        log.info("no unplayed fixtures in the mart; no forecasts written")
    else:
        for name, features in MODEL_FEATURES.items():
            _, forecast = train_one(played, future, features, seed=primary_seed, categories=levels)
            prediction_rows.extend(_prediction_rows(future, name, run_date, forecast, None))
        log.info(
            "forecasts: %d unplayed fixtures per model, refit on all %d played rows",
            len(future),
            len(played),
        )

    # 6. run-to-run variance: the other seeds on the same split
    for seed in seed_list[1:]:
        for name, features in MODEL_FEATURES.items():
            _, predicted = train_one(train, test, features, seed=seed, categories=levels)
            mae = compute_metrics(y_test, predicted, n_train=len(train)).mae
            variance_rows.append(
                {"model_name": name, "run_date": run_date, "seed": seed, "mae": mae}
            )
            summary["variance"][name][seed] = mae
    for name in MODEL_FEATURES:
        maes = list(summary["variance"][name].values())
        spread = max(maes) - min(maes)
        log.info(
            "variance %s: %d seed(s), MAE min %.1f max %.1f spread %.1f%s",
            name,
            len(maes),
            min(maes),
            max(maes),
            spread,
            # Without subsample or colsample_* in XGB_PARAMS the hist booster is
            # deterministic and the seed changes nothing; say so rather than
            # let a zero read as "no noise".
            " (identical across seeds: no subsampling in config.XGB_PARAMS, so the seed "
            "has nothing to vary)"
            if len(maes) > 1 and spread == 0.0
            else "",
        )

    # 7. expanding-window folds by season
    folds = season_folds(played)
    for fold_season, fold_train, fold_test in folds:
        y_fold = pd.to_numeric(fold_test[config.TARGET], errors="raise").astype("float64")
        fold_predictions: dict[str, pd.Series] = {}
        for name, features in MODEL_FEATURES.items():
            _, fold_predictions[name] = train_one(
                fold_train, fold_test, features, seed=primary_seed, categories=levels
            )
        fold_predictions[NAIVE_MODEL_NAME] = naive_club_mean(fold_train, fold_test)
        for name, predicted in fold_predictions.items():
            metrics = compute_metrics(y_fold, predicted, n_train=len(fold_train))
            cv_rows.append(
                {
                    "model_name": name,
                    "run_date": run_date,
                    "fold_season": fold_season,
                    **_metrics_row(name, run_date, metrics),
                }
            )
            summary["cv"].setdefault(name, {})[fold_season] = metrics.mae
            log.info(
                "cv fold %s %s: MAE %.1f (train %d rows, test %d rows)",
                fold_season,
                name,
                metrics.mae,
                len(fold_train),
                len(fold_test),
            )
    if not folds:
        log.info("cv: one season in the mart, no expanding-window folds")

    # write everything, upserting on the primary keys
    written = {
        "predictions": _upsert(con, "predictions", pd.DataFrame(prediction_rows)),
        "model_metrics": _upsert(con, "model_metrics", pd.DataFrame(metrics_rows)),
        "feature_importance": _upsert(con, "feature_importance", pd.DataFrame(importance_rows)),
        "model_variance": _upsert(con, "model_variance", pd.DataFrame(variance_rows)),
        "model_cv": _upsert(con, "model_cv", pd.DataFrame(cv_rows)),
    }
    summary["rows_written"] = written
    log.info(
        "train: wrote %s for run_date %s",
        ", ".join(f"{table}={n}" for table, n in written.items()),
        run_date,
    )
    return summary


def _log_metrics(name: str, metrics: ErrorMetrics) -> None:
    log.info(
        "model %-16s MAE %.1f  MAPE %.3f  RMSE %.1f  (train %d, test %d)",
        name,
        metrics.mae,
        metrics.mape,
        metrics.rmse,
        metrics.n_train,
        metrics.n_test,
    )


def _metrics_row(name: str, run_date: dt.date, metrics: ErrorMetrics) -> dict[str, Any]:
    return {
        "model_name": name,
        "run_date": run_date,
        "mae": float(metrics.mae),
        "mape": float(metrics.mape),
        "rmse": float(metrics.rmse),
        "n_train": int(metrics.n_train),
        "n_test": int(metrics.n_test),
    }


def _prediction_rows(
    rows: pd.DataFrame,
    name: str,
    run_date: dt.date,
    predicted: pd.Series,
    actual: pd.Series | None,
) -> list[dict[str, Any]]:
    """Prediction rows for one model over one set of matches.

    Args:
        rows: The matches, carrying match_id.
        name: Model name.
        run_date: This run.
        predicted: Predictions aligned to rows' index.
        actual: The actuals aligned to rows' index, or None for forecasts.
    """
    aligned = predicted.reindex(rows.index)
    actuals = None if actual is None else actual.reindex(rows.index)
    out: list[dict[str, Any]] = []
    for i, match_id in enumerate(rows["match_id"].tolist()):
        out.append(
            {
                "match_id": str(match_id),
                "model_name": name,
                "run_date": run_date,
                "predicted": float(aligned.iloc[i]),
                "actual": None if actuals is None else float(actuals.iloc[i]),
            }
        )
    return out


def _upsert(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame) -> int:
    """INSERT ... SELECT from a registered frame, updating on the primary key.

    Args:
        con: Open connection with write access.
        table: One of the five output tables.
        frame: Rows with the table's columns. Empty writes nothing.

    Returns:
        Rows written.
    """
    columns = _TABLE_COLUMNS[table]
    keys = _TABLE_KEYS[table]
    if frame.empty:
        return 0
    names = [c for c, _ in columns]
    view = f"_usl_train_{table}"
    con.register(view, frame[names])
    try:
        select = ", ".join(f'CAST("{c}" AS {sql_type}) AS "{c}"' for c, sql_type in columns)
        updates = ", ".join(f'"{c}" = excluded."{c}"' for c in names if c not in keys)
        con.execute(
            f'INSERT INTO "{table}" ({", ".join(names)}) '
            f"SELECT {select} FROM {view} "
            f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}"
        )
    finally:
        con.unregister(view)
    return int(len(frame))


def _as_date(value: object) -> dt.date:
    """A DATE that came back from DuckDB as a Timestamp, as a plain date."""
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return pd.Timestamp(str(value)).date()
