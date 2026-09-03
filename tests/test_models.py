"""Model training: the split and the importance extraction.

No accuracy assertions. There is nothing to pin - a test that requires MAE below
a threshold fails the day the data changes, for no reason.

Doc: docs/phases/07-two-models.md
"""

from __future__ import annotations

import pandas as pd
import pytest

from usl.models.train import chronological_split


def test_split_is_chronological_not_random() -> None:
    """Every training date is at or before every test date.

    train_test_split with a random seed trains on the following week's data,
    which is leakage that produces validation error you cannot trust.
    """
    df = pd.DataFrame({"date": pd.date_range("2024-03-01", periods=100, freq="D")})
    train, test = chronological_split(df, test_fraction=0.2)
    assert train["date"].max() <= test["date"].min()


def test_split_respects_the_test_fraction() -> None:
    """Twenty percent means twenty percent."""
    df = pd.DataFrame({"date": pd.date_range("2024-03-01", periods=100, freq="D")})
    train, test = chronological_split(df, test_fraction=0.2)
    assert len(test) == 20
    assert len(train) == 80


def test_importance_includes_zero_importance_features() -> None:
    """get_score omits features the model never split on.

    A zero-importance feature must appear as a zero, not as an absence, or the
    dashboard shows a gap where it should show a zero. "The model ignored this
    feature entirely" is one of the more interesting things the chart can say
    about points_from_relegation_line.
    """
    pytest.skip("TODO: fit on a fixture where one feature is constant, assert it is present with 0")


def test_naive_baseline_is_written_to_model_metrics() -> None:
    """The comparison belongs in the table, not in your head.

    If XGBoost cannot beat the club mean, you want to be the one who noticed.
    """
    pytest.skip("TODO")


def test_rerunning_on_the_same_date_updates_rather_than_duplicates() -> None:
    """The primary keys enforce it; the write has to be an upsert to honour them."""
    pytest.skip("TODO")


def test_both_models_read_the_same_rows() -> None:
    """Same mart, same rows, same split. Only the column selection differs.

    If this fails, any difference in error between the models is unattributable
    and the headline question cannot be answered.
    """
    pytest.skip("TODO")
