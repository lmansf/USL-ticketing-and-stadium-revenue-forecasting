"""Train both models and write the three output tables.

One dataset, two feature lists, no duplicated tables. The split happens in code,
by column selection - see usl/features/definitions.py.

See docs/phases/07-two-models.md
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

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
# Keyed by model_name and run_date so Tableau gets a filter that toggles models
# or overlays both, and so everything is a time series from the first run.
# Do NOT overwrite these each week. The accumulating history is the asset - week
# four is when they start being interesting, and you cannot backfill a
# measurement you did not take.


def ensure_output_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create predictions, model_metrics, and feature_importance if absent.

    Args:
        con: Open connection with write access.

    TODO: implement.
    """
    raise NotImplementedError("TODO")


def load_training_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Read mart_match_features, applying the COVID exclusion.

    2020 attendance is not demand signal. config.DROP_COVID defaults to on;
    flipping it is how you show the difference.

    Args:
        con: Open connection.

    Returns:
        One row per match, sorted by date.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/06-features.md#covid")


def chronological_split(
    df: pd.DataFrame, test_fraction: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by date, not at random.

    Predicting attendance for a match while trained on the following week's data
    is leakage, and train_test_split with a random seed does exactly that.

    Args:
        df: Feature frame, sorted by date.
        test_fraction: Proportion held out, from the end.

    Returns:
        (train, test).

    TODO: implement. Consider expanding-window CV by season as the stronger
    version - a single holdout of fifty matches is a noisy estimate.
    """
    raise NotImplementedError("TODO: see docs/phases/07-two-models.md#validation")


def train_one(
    train: pd.DataFrame, test: pd.DataFrame, features: tuple[str, ...]
) -> tuple[object, pd.Series]:
    """Fit one XGBoost model and predict the holdout.

    Args:
        train: Training rows.
        test: Holdout rows.
        features: The column selection defining this model.

    Returns:
        (fitted model, predictions aligned to test's index).

    TODO: implement using config.XGB_PARAMS.
    """
    raise NotImplementedError("TODO")


def extract_importance(model: object, features: tuple[str, ...]) -> pd.DataFrame:
    """Pull feature importance from a fitted booster.

    Use gain, not weight. Weight counts splits, which flatters high-cardinality
    features - opponent_club_id would top the chart on split count alone and tell
    you nothing.

    One trap: get_score omits features the model never split on, so a
    zero-importance feature is MISSING from the dict rather than present with a
    zero. Reindex against the full feature list before returning, or the
    dashboard shows a gap where it should show a zero. "The model ignored this
    feature entirely" is one of the more interesting things the chart can say
    about points_from_relegation_line.

    Args:
        model: Fitted XGBRegressor.
        features: The full feature list for this model, for reindexing.

    Returns:
        Columns: feature, importance, is_prorel. One row per feature, including
        the zeros.

    TODO: implement. See docs/phases/07-two-models.md, exercise 7.1.
    """
    raise NotImplementedError("TODO: see docs/phases/07-two-models.md, exercise 7.1")


def train_all(con: duckdb.DuckDBPyConnection, run_date: dt.date | None = None) -> None:
    """Train every model, plus the naive baseline, and write all three tables.

    Feature importance says a feature was used. Error says it helped. Log both
    per run so the dashboard can show either, and so the headline question has
    two independent lines of evidence rather than one.

    Args:
        con: Open connection with write access.
        run_date: Defaults to today. Explicit for testing and for backfilling a
            missed run.

    TODO: implement. Re-running on the same date must update rather than
    duplicate - the primary keys enforce it, so use an upsert.
    """
    raise NotImplementedError("TODO: see docs/phases/07-two-models.md, exercise 7.1")
