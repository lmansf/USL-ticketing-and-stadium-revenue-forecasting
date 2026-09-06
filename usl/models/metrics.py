"""Error metrics and the naive baseline.

See docs/phases/07-two-models.md
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd
from sklearn.metrics import (
    mean_absolute_error,
    mean_absolute_percentage_error,
    root_mean_squared_error,
)

from usl import config

log = logging.getLogger(__name__)


@dataclass
class ErrorMetrics:
    """Holdout error for one model on one run.

    Attributes:
        mae: Mean absolute error, in attendees. The headline number, because it
            is in the units of the thing being predicted.
        mape: Mean absolute percentage error. Comparable across clubs of very
            different size, which MAE is not.
        rmse: Root mean squared error. Penalises large misses, so a gap between
            RMSE and MAE tells you the errors are concentrated rather than spread.
        n_train: Training row count.
        n_test: Holdout row count.
    """

    mae: float
    mape: float
    rmse: float
    n_train: int
    n_test: int


def compute_metrics(y_true: pd.Series, y_pred: pd.Series, *, n_train: int) -> ErrorMetrics:
    """Compute holdout error metrics.

    MAPE divides by the actual, so a zero gate would make it infinite and one
    behind-closed-doors match would swamp every other row. It is therefore
    computed over the rows with a positive actual only, and is NaN when there
    are none. MAE and RMSE use every row.

    Args:
        y_true: Actual attendance. Nulls are an error - an unplayed row has no
            business in a holdout.
        y_pred: Predicted attendance. When both are Series the prediction is
            aligned to y_true's index, so a misaligned Series fails loudly
            rather than scoring the wrong match.
        n_train: Training row count, carried through for the metrics table.

    Returns:
        ErrorMetrics.

    Raises:
        ValueError: Empty holdout, mismatched lengths, or a null on either side.
    """
    actual = pd.to_numeric(pd.Series(y_true), errors="raise").astype("float64")
    predicted_raw = pd.Series(y_pred)
    if isinstance(y_true, pd.Series) and isinstance(y_pred, pd.Series):
        predicted_raw = predicted_raw.reindex(actual.index)
    if len(predicted_raw) != len(actual):
        raise ValueError(
            f"y_true has {len(actual)} rows but y_pred has {len(predicted_raw)}; "
            "the two must cover the same holdout rows"
        )
    predicted = pd.to_numeric(predicted_raw, errors="raise").astype("float64")
    if actual.empty:
        raise ValueError("no holdout rows to score")
    if actual.isna().any():
        raise ValueError(f"{int(actual.isna().sum())} holdout rows have no actual attendance")
    if predicted.isna().any():
        raise ValueError(
            f"{int(predicted.isna().sum())} holdout rows have no prediction - "
            "the predictions are not aligned to the holdout"
        )

    mae = float(mean_absolute_error(actual, predicted))
    rmse = float(root_mean_squared_error(actual, predicted))
    positive = actual > 0
    if positive.any():
        mape = float(mean_absolute_percentage_error(actual[positive], predicted[positive]))
    else:
        mape = math.nan
    return ErrorMetrics(
        mae=mae, mape=mape, rmse=rmse, n_train=int(n_train), n_test=int(len(actual))
    )


def naive_club_mean(train: pd.DataFrame, test: pd.DataFrame) -> pd.Series:
    """Predict each match as the club's mean home attendance in the training set.

    Log this alongside both models every run. Attendance is dominated by which
    club is at home, that is mostly stable within a season, and this predictor is
    genuinely hard to beat.

    It cuts both ways. If XGBoost cannot beat it, you want to be the one who
    noticed. If XGBoost beats it by a wide margin, suspect leakage before you
    celebrate.

    A club in the holdout with no training rows - an expansion club, or a club
    whose first home match falls after the split - has no mean of its own. It
    gets the training-set overall mean: the same "you know nothing about this
    club" prior XGBoost effectively has for an unseen category, so the two are
    being compared on equal terms. The alternative, dropping the row, would
    score the baseline on an easier holdout than the models.

    Args:
        train: Training rows, with home_club_id and the target.
        test: Holdout rows to predict.

    Returns:
        Predictions aligned to test's index, float64, never null.
    """
    gates = pd.to_numeric(train[config.TARGET], errors="raise").astype("float64")
    club_means = gates.groupby(train["home_club_id"]).mean()
    overall = float(gates.mean())
    predicted = test["home_club_id"].map(club_means).astype("float64")
    missing = predicted.isna()
    if missing.any():
        clubs = sorted(str(c) for c in test.loc[missing, "home_club_id"].unique())
        log.info(
            "naive_club_mean: %d holdout rows for clubs with no training rows (%s) "
            "fall back to the training mean of %.0f",
            int(missing.sum()),
            ", ".join(clubs),
            overall,
        )
        predicted = predicted.fillna(overall)
    return pd.Series(predicted.to_numpy(), index=test.index, name="predicted", dtype="float64")
