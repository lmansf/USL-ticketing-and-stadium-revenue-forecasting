"""Error metrics and the naive baseline.

See docs/phases/07-two-models.md
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


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

    Args:
        y_true: Actual attendance.
        y_pred: Predicted attendance.
        n_train: Training row count, carried through for the metrics table.

    Returns:
        ErrorMetrics.

    TODO: implement.
    """
    raise NotImplementedError("TODO")


def naive_club_mean(train: pd.DataFrame, test: pd.DataFrame) -> pd.Series:
    """Predict each match as the club's mean home attendance in the training set.

    Log this alongside both models every run. Attendance is dominated by which
    club is at home, that is mostly stable within a season, and this predictor is
    genuinely hard to beat.

    It cuts both ways. If XGBoost cannot beat it, you want to be the one who
    noticed. If XGBoost beats it by a wide margin, suspect leakage before you
    celebrate.

    Args:
        train: Training rows, with home_club_id and the target.
        test: Holdout rows to predict.

    Returns:
        Predictions aligned to test's index.

    TODO: implement. Decide what to do about a club present in test but not in
    train - an expansion club in the holdout has no mean to fall back on.
    """
    raise NotImplementedError("TODO: see docs/phases/07-two-models.md#validation")
