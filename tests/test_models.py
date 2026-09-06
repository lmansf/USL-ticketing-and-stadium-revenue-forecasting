"""Model training, the metrics, the naive baseline, and the Tableau export.

No accuracy assertions. There is nothing to pin - a test that requires MAE below
a threshold fails the day the data changes, for no reason. What is pinned is
the contract: the split is chronological, the two models see the same rows,
zero-importance features are zeros, reruns update rather than duplicate, and
every model name that should be in a table is.

The mart here is synthetic and built in this file, deliberately: these tests
must not depend on the SQL layer. It has exactly the columns of
usl.features.definitions.mart_columns(), plausible types, one feature that is
constant everywhere (points_from_relegation_line, so its importance must come
out as a present zero), and a few unplayed fixtures at the end.

Doc: docs/phases/07-two-models.md, docs/phases/08-tableau.md
"""

from __future__ import annotations

import datetime as dt
import math
import re
import sys
import types
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from usl import config
from usl.export.extracts import (
    BAND_FILE_STEM,
    BAND_LABEL,
    export_all,
    export_csv,
    export_hyper,
)
from usl.features.definitions import MODEL_FEATURES, PROREL_FEATURES, mart_columns
from usl.models.metrics import compute_metrics, naive_club_mean
from usl.models.train import (
    NAIVE_MODEL_NAME,
    categorical_levels,
    chronological_split,
    extract_importance,
    load_training_frame,
    prepare_features,
    season_folds,
    train_all,
    train_one,
)

OUTPUT_TABLES = ("predictions", "model_metrics", "feature_importance", "model_cv", "model_variance")
ALL_MODEL_NAMES = frozenset(MODEL_FEATURES) | {NAIVE_MODEL_NAME}

CLUBS = tuple(f"club_{c}" for c in "abcdef")
SEASONS = (2023, 2024)
DERBY = frozenset({"club_a", "club_b"})
# Each club's underlying draw, so the club mean is a real signal for the naive
# baseline to pick up and the models have something to learn.
CLUB_BASE = {club: 3000 + 900 * i for i, club in enumerate(CLUBS)}
N_FUTURE = 3
FAST_XGB_PARAMS: dict[str, object] = {
    "n_estimators": 20,
    "max_depth": 3,
    "learning_rate": 0.3,
    "random_state": 42,
}


# ---------------------------------------------------------------------------
# The synthetic mart
# ---------------------------------------------------------------------------


def synthetic_mart(*, n_future: int = N_FUTURE, seed: int = 0) -> pd.DataFrame:
    """Six clubs, two seasons of every ordered pairing, exactly mart_columns().

    Thirty fixtures a season in ten weekly rounds of three, every third round
    on a Tuesday. Attendance is a club base plus a weekend bonus, a derby bonus
    and a small penalty per place in the table, with noise from a seeded
    RandomState. The last n_future fixtures of the final season are unplayed:
    attendance null, is_played false. points_from_relegation_line is 0 on
    every row.
    """
    rng = np.random.RandomState(seed)
    rows: list[dict[str, object]] = []
    last_gate: dict[str, list[int]] = {club: [] for club in CLUBS}
    previous_season_gate: dict[tuple[int, str, str], int] = {}
    for season in SEASONS:
        fixtures = [(h, a) for h in CLUBS for a in CLUBS if h != a]
        rng.shuffle(fixtures)
        # first Saturday of March
        start = dt.date(season, 3, 1)
        start += dt.timedelta(days=(5 - start.weekday()) % 7)
        dated: list[tuple[dt.date, str, str]] = []
        for i, (home, away) in enumerate(fixtures):
            round_no = i // 3
            date = start + dt.timedelta(days=7 * round_no + (3 if round_no % 3 == 2 else 0))
            dated.append((date, home, away))
        dated.sort()
        home_dates = {club: sorted(d for d, h, _ in dated if h == club) for club in CLUBS}
        club_dates = {club: sorted(d for d, h, a in dated if club in (h, a)) for club in CLUBS}
        for date, home, away in dated:
            dow = date.isoweekday() % 7  # DuckDB's convention: 0 = Sunday
            is_weekend = dow in (0, 6)
            rank_before = int(rng.randint(1, len(CLUBS) + 1))
            opponent_rank = int(rng.randint(1, len(CLUBS) + 1))
            is_derby = {home, away} == DERBY
            gate = (
                CLUB_BASE[home]
                + (800 if is_weekend else 0)
                + (500 if is_derby else 0)
                - 60 * rank_before
                + int(rng.normal(0, 200))
            )
            history = last_gate[home]
            live = bool(rng.rand() > 0.2)
            final_gate = max(gate, 500)
            rows.append(
                {
                    "match_id": f"nk:{season}-{home}-{away}",
                    "season": season,
                    "date": date,
                    "home_club_id": home,
                    "attendance": final_gate,
                    "is_played": True,
                    "is_covid_affected": False,
                    "day_of_week": dow,
                    "month": date.month,
                    "is_weekend": is_weekend,
                    "is_midweek": dow in (2, 3, 4),
                    "last_home_gate": history[-1] if history else None,
                    "home_gate_ma3": float(np.mean(history[-3:])) if history else None,
                    "home_gate_ma5": float(np.mean(history[-5:])) if history else None,
                    "same_fixture_last_season": previous_season_gate.get((season - 1, home, away)),
                    "opponent_club_id": away,
                    "is_derby": is_derby,
                    "matches_remaining": sum(1 for d in club_dates[home] if d >= date),
                    "is_season_opener": date == home_dates[home][0],
                    "is_final_home_match": date == home_dates[home][-1],
                    "rank_before": rank_before,
                    "opponent_rank_before": opponent_rank,
                    "rank_gap": opponent_rank - rank_before,
                    "points_from_playoff_line": int(rng.randint(-6, 10)),
                    "is_mathematically_live": live,
                    "matches_since_elimination": -1 if live else int(rng.randint(0, 3)),
                    "points_from_relegation_line": 0,
                }
            )
            history.append(final_gate)
            previous_season_gate[(season, home, away)] = final_gate

    for row in rows:
        assert set(row) == set(mart_columns()), "synthetic row does not match mart_columns()"
    frame = pd.DataFrame(rows, columns=list(mart_columns()))
    frame = frame.sort_values(["date", "match_id"]).reset_index(drop=True)
    for col in ("attendance", "last_home_gate", "same_fixture_last_season"):
        frame[col] = frame[col].astype("Int64")
    if n_future:
        tail = frame.index[-n_future:]
        frame.loc[tail, "attendance"] = pd.NA
        frame.loc[tail, "is_played"] = False
    frame["date"] = pd.to_datetime(frame["date"])
    frame["is_played"] = frame["is_played"].astype(bool)
    return frame


def write_mart(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame) -> None:
    """Materialise a synthetic frame as mart_match_features with the mart's types."""
    con.register("_synthetic_mart", frame)
    con.execute(
        """
        CREATE OR REPLACE TABLE mart_match_features AS
        SELECT * REPLACE (
            CAST(date AS DATE)                              AS date,
            CAST(attendance AS INTEGER)                     AS attendance,
            CAST(last_home_gate AS INTEGER)                 AS last_home_gate,
            CAST(same_fixture_last_season AS INTEGER)       AS same_fixture_last_season,
            CAST(day_of_week AS INTEGER)                    AS day_of_week,
            CAST(month AS INTEGER)                          AS month,
            CAST(matches_remaining AS INTEGER)              AS matches_remaining,
            CAST(rank_before AS INTEGER)                    AS rank_before,
            CAST(opponent_rank_before AS INTEGER)           AS opponent_rank_before,
            CAST(rank_gap AS INTEGER)                       AS rank_gap,
            CAST(points_from_playoff_line AS INTEGER)       AS points_from_playoff_line,
            CAST(matches_since_elimination AS INTEGER)      AS matches_since_elimination,
            CAST(points_from_relegation_line AS INTEGER)    AS points_from_relegation_line
        )
        FROM _synthetic_mart
        """
    )
    con.unregister("_synthetic_mart")


def counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Row count per output table."""
    out: dict[str, int] = {}
    for table in OUTPUT_TABLES:
        row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
        out[table] = int(row[0]) if row else 0
    return out


@pytest.fixture
def fast_xgb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small trees and two seeds, so a full train_all runs in a couple of seconds."""
    monkeypatch.setattr(config, "XGB_PARAMS", dict(FAST_XGB_PARAMS))
    monkeypatch.setattr(config, "VARIANCE_SEEDS", (42, 7))


@pytest.fixture
def mart_con(con: duckdb.DuckDBPyConnection, fast_xgb: None) -> duckdb.DuckDBPyConnection:
    """An in-memory connection holding the synthetic mart."""
    write_mart(con, synthetic_mart())
    return con


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


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


def test_split_sorts_by_date_and_keeps_the_original_index() -> None:
    """A frame handed over out of date order is still split by date, and the
    index values survive so predictions can be aligned back to their rows.
    Ties on date keep their incoming order (the sort is stable)."""
    dates = pd.to_datetime(["2024-03-09", "2024-03-02", "2024-03-02", "2024-03-16", "2024-03-23"])
    df = pd.DataFrame({"date": dates, "tag": list("abcde")}, index=[50, 40, 30, 20, 10])
    train, test = chronological_split(df, test_fraction=0.4)
    assert list(train["tag"]) == ["b", "c", "a"]
    assert list(train.index) == [40, 30, 50]
    assert list(test.index) == [20, 10]
    with pytest.raises(ValueError):
        chronological_split(df, test_fraction=1.0)


def test_season_folds_are_expanding_windows() -> None:
    """One fold per season after the first: train on everything earlier, test on it.

    Two seasons give one fold, whose training rows are all of 2023 and whose
    test rows are all of 2024. One season gives no folds at all, which is why
    model_cv may legitimately be empty.
    """
    frame = synthetic_mart(n_future=0)
    folds = season_folds(frame)
    assert [season for season, _, _ in folds] == [2024]
    _, train, test = folds[0]
    assert set(train["season"]) == {2023}
    assert set(test["season"]) == {2024}
    assert len(train) + len(test) == len(frame)
    assert season_folds(frame[frame["season"] == 2023]) == []


# ---------------------------------------------------------------------------
# The model matrix and one fit
# ---------------------------------------------------------------------------


def test_prepare_features_types_and_shared_categories(fast_xgb: None) -> None:
    """Booleans to int, numbers to float, nulls left as NaN, and opponent_club_id
    a categorical whose codes mean the same thing in every subset.

    A club that only appears in the holdout would otherwise get a code the
    model had never seen under a different meaning.
    """
    frame = synthetic_mart()
    levels = categorical_levels(frame)
    assert levels == {"opponent_club_id": sorted(CLUBS)}

    features = MODEL_FEATURES["prorel"]
    matrix = prepare_features(frame, features, categories=levels)
    assert list(matrix.columns) == list(features)
    assert matrix.index.equals(frame.index)
    assert str(matrix["is_weekend"].dtype) == "int64"
    assert set(matrix["is_weekend"].unique()) <= {0, 1}
    assert str(matrix["rank_before"].dtype) == "float64"
    assert str(matrix["opponent_club_id"].dtype) == "category"
    # a club's first home match has no last gate, and that stays a NaN
    first_home = frame["is_season_opener"] & (frame["season"] == SEASONS[0])
    assert matrix.loc[first_home, "last_home_gate"].isna().all()
    assert matrix.loc[~first_home, "last_home_gate"].notna().all()

    # the same club gets the same code in a subset built from the full levels
    subset = frame[frame["opponent_club_id"] == "club_f"].head(4)
    sub_matrix = prepare_features(subset, features, categories=levels)
    full_codes = matrix["opponent_club_id"].cat.codes.loc[subset.index]
    assert list(sub_matrix["opponent_club_id"].cat.codes) == list(full_codes)
    assert list(sub_matrix["opponent_club_id"].cat.categories) == sorted(CLUBS)


def test_train_one_reads_config_at_call_time_and_aligns_to_test(fast_xgb: None) -> None:
    """XGB_PARAMS is read when train_one runs, the seed argument overrides
    random_state, and the predictions carry the holdout's own index."""
    frame = synthetic_mart(n_future=0)
    train, test = chronological_split(frame, config.TEST_FRACTION)
    model, predicted = train_one(train, test, MODEL_FEATURES["baseline"], seed=7)
    assert model.get_params()["n_estimators"] == FAST_XGB_PARAMS["n_estimators"]
    assert model.get_params()["random_state"] == 7
    assert predicted.index.equals(test.index)
    assert predicted.notna().all()
    assert str(predicted.dtype) == "float64"


def test_importance_includes_zero_importance_features(fast_xgb: None) -> None:
    """get_score omits features the model never split on.

    A zero-importance feature must appear as a zero, not as an absence, or the
    dashboard shows a gap where it should show a zero. "The model ignored this
    feature entirely" is one of the more interesting things the chart can say
    about points_from_relegation_line - which is constant in the synthetic mart
    and therefore cannot be split on.
    """
    frame = synthetic_mart(n_future=0)
    train, test = chronological_split(frame, config.TEST_FRACTION)
    features = MODEL_FEATURES["prorel"]
    model, _ = train_one(train, test, features)
    importance = extract_importance(model, features)

    assert list(importance.columns) == ["feature", "importance", "is_prorel"]
    assert list(importance["feature"]) == list(features)
    assert importance["feature"].is_unique
    constant = importance.set_index("feature").loc["points_from_relegation_line"]
    assert constant["importance"] == 0.0
    assert bool(constant["is_prorel"]) is True
    assert (importance["importance"] > 0).any()
    assert set(importance.loc[importance["is_prorel"], "feature"]) == set(PROREL_FEATURES)
    # the raw booster dict really does leave the constant feature out
    raw = model.get_booster().get_score(importance_type=config.IMPORTANCE_TYPE)
    assert "points_from_relegation_line" not in raw


# ---------------------------------------------------------------------------
# Metrics and the naive baseline
# ---------------------------------------------------------------------------


def test_mape_ignores_zero_actuals() -> None:
    """A zero gate would make MAPE infinite and swamp every other row.

    MAPE is computed over rows with a positive actual only; MAE and RMSE use
    every row. With no positive actuals at all MAPE is NaN rather than an
    error.
    """
    y_true = pd.Series([0.0, 100.0, 200.0], index=[3, 4, 5])
    y_pred = pd.Series([50.0, 110.0, 180.0], index=[3, 4, 5])
    metrics = compute_metrics(y_true, y_pred, n_train=7)
    assert metrics.mae == pytest.approx((50 + 10 + 20) / 3)
    assert metrics.rmse == pytest.approx(math.sqrt((50**2 + 10**2 + 20**2) / 3))
    assert metrics.mape == pytest.approx((10 / 100 + 20 / 200) / 2)
    assert (metrics.n_train, metrics.n_test) == (7, 3)
    zeros = compute_metrics(pd.Series([0.0, 0.0]), pd.Series([1.0, 2.0]), n_train=1)
    assert math.isnan(zeros.mape)
    assert zeros.mae == pytest.approx(1.5)


def test_metrics_reject_nulls_and_misaligned_predictions() -> None:
    """An unplayed row has no business in a holdout, and a prediction Series
    that does not cover the holdout's index fails rather than scoring the
    wrong match."""
    with pytest.raises(ValueError):
        compute_metrics(pd.Series([100.0, None]), pd.Series([1.0, 2.0]), n_train=1)
    with pytest.raises(ValueError):
        compute_metrics(
            pd.Series([100.0, 200.0], index=[1, 2]), pd.Series([1.0], index=[1]), n_train=1
        )
    with pytest.raises(ValueError):
        compute_metrics(pd.Series([], dtype="float64"), pd.Series([], dtype="float64"), n_train=1)


def test_naive_club_mean_falls_back_for_a_club_absent_from_training() -> None:
    """A club in the holdout with no training rows gets the training-set mean.

    club_a averages 5000 in training and club_b 3000, so the overall mean is
    4000. club_z never appears in training; dropping the row would score the
    baseline on an easier holdout than the models.
    """
    train = pd.DataFrame(
        {"home_club_id": ["club_a", "club_a", "club_b"], "attendance": [4000, 6000, 3000]}
    )
    test = pd.DataFrame({"home_club_id": ["club_b", "club_z", "club_a"]}, index=[10, 20, 30])
    predicted = naive_club_mean(train, test)
    assert list(predicted.index) == [10, 20, 30]
    assert predicted.tolist() == pytest.approx([3000.0, 13000 / 3, 5000.0])
    assert predicted.notna().all()


# ---------------------------------------------------------------------------
# train_all end to end on the synthetic mart
# ---------------------------------------------------------------------------


def test_load_training_frame_drops_covid_rows_when_configured(
    mart_con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.DROP_COVID is read at call time, and unplayed rows are kept."""
    total = mart_con.execute("SELECT count(*) FROM mart_match_features").fetchone()
    assert total is not None
    mart_con.execute(
        "UPDATE mart_match_features SET is_covid_affected = true "
        "WHERE match_id IN (SELECT match_id FROM mart_match_features ORDER BY date LIMIT 4)"
    )
    monkeypatch.setattr(config, "DROP_COVID", True)
    dropped = load_training_frame(mart_con)
    assert len(dropped) == total[0] - 4
    assert not dropped["is_covid_affected"].any()
    assert dropped["date"].is_monotonic_increasing
    assert list(dropped.index) == list(range(len(dropped)))
    assert int((~dropped["is_played"]).sum()) == N_FUTURE
    assert list(dropped.columns) == list(mart_columns())

    monkeypatch.setattr(config, "DROP_COVID", False)
    kept = load_training_frame(mart_con)
    assert len(kept) == total[0]


def test_naive_baseline_is_written_to_model_metrics(mart_con: duckdb.DuckDBPyConnection) -> None:
    """The comparison belongs in the table, not in your head.

    If XGBoost cannot beat the club mean, you want to be the one who noticed.
    """
    run_date = dt.date(2024, 9, 3)
    summary = train_all(mart_con, run_date)
    rows = mart_con.execute(
        "SELECT model_name, run_date, mae, mape, rmse, n_train, n_test "
        "FROM model_metrics ORDER BY model_name"
    ).fetchall()
    assert {r[0] for r in rows} == ALL_MODEL_NAMES
    naive = next(r for r in rows if r[0] == NAIVE_MODEL_NAME)
    assert naive[1] == run_date
    assert naive[2] > 0 and naive[3] > 0 and naive[4] >= naive[2]
    assert naive[5] == summary["n_train"] and naive[6] == summary["n_test"]
    assert summary["mae"][NAIVE_MODEL_NAME] == pytest.approx(naive[2])
    assert set(summary["mae"]) == ALL_MODEL_NAMES
    # the split date is the first holdout date: played rows sorted by date,
    # the last round(n * TEST_FRACTION) of them held out
    played = synthetic_mart()
    played = played[played["is_played"]].sort_values(["date", "match_id"])
    n_test = round(len(played) * config.TEST_FRACTION)
    assert (summary["n_train"], summary["n_test"]) == (len(played) - n_test, n_test)
    assert summary["split_date"] == played["date"].iloc[len(played) - n_test].date()
    assert summary["n_future"] == N_FUTURE
    # a naive prediction is the club's training mean, so it is written too
    naive_predictions = mart_con.execute(
        "SELECT count(*) FROM predictions WHERE model_name = ?", [NAIVE_MODEL_NAME]
    ).fetchone()
    assert naive_predictions is not None and naive_predictions[0] == summary["n_test"]


def test_rerunning_on_the_same_date_updates_rather_than_duplicates(
    mart_con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The primary keys enforce it; the write has to be an upsert to honour them.

    A second run on the same date leaves every row count unchanged and the
    metrics reflect the second run; a run on a new date adds history.
    """
    run_date = dt.date(2024, 9, 3)
    train_all(mart_con, run_date)
    first = counts(mart_con)
    assert all(n > 0 for n in first.values()), first

    monkeypatch.setattr(config, "XGB_PARAMS", {**FAST_XGB_PARAMS, "n_estimators": 5})
    second = train_all(mart_con, run_date)
    assert counts(mart_con) == first
    stored = mart_con.execute(
        "SELECT mae FROM model_metrics WHERE model_name = 'baseline' AND run_date = ?", [run_date]
    ).fetchone()
    assert stored is not None
    assert stored[0] == pytest.approx(second["mae"]["baseline"])

    train_all(mart_con, run_date + dt.timedelta(days=7))
    assert counts(mart_con) == {table: 2 * n for table, n in first.items()}


def test_both_models_read_the_same_rows(mart_con: duckdb.DuckDBPyConnection) -> None:
    """Same mart, same rows, same split. Only the column selection differs.

    If this fails, any difference in error between the models is unattributable
    and the headline question cannot be answered.
    """
    run_date = dt.date(2024, 9, 3)
    summary = train_all(mart_con, run_date)
    by_model = {
        name: set(
            r[0]
            for r in mart_con.execute(
                "SELECT match_id FROM predictions WHERE model_name = ?", [name]
            ).fetchall()
        )
        for name in ALL_MODEL_NAMES
    }
    assert by_model["baseline"] == by_model["prorel"]
    assert len(by_model["baseline"]) == summary["n_test"] + summary["n_future"]
    assert by_model[NAIVE_MODEL_NAME] < by_model["baseline"]
    assert len(by_model[NAIVE_MODEL_NAME]) == summary["n_test"]

    sizes = mart_con.execute("SELECT DISTINCT n_train, n_test FROM model_metrics").fetchall()
    assert sizes == [(summary["n_train"], summary["n_test"])]
    assert summary["feature_count"] == {
        name: len(features) for name, features in MODEL_FEATURES.items()
    }


def test_future_rows_get_forecasts_with_null_actual(mart_con: duckdb.DuckDBPyConnection) -> None:
    """Unplayed fixtures are forecast by a refit on every played row and written
    with actual null, for both XGBoost models and not for the naive baseline."""
    train_all(mart_con, dt.date(2024, 9, 3))
    future_ids = {
        r[0]
        for r in mart_con.execute(
            "SELECT match_id FROM mart_match_features WHERE NOT is_played"
        ).fetchall()
    }
    assert len(future_ids) == N_FUTURE
    rows = mart_con.execute(
        "SELECT match_id, model_name, predicted, actual FROM predictions WHERE actual IS NULL"
    ).fetchall()
    assert {r[0] for r in rows} == future_ids
    assert {r[1] for r in rows} == set(MODEL_FEATURES)
    assert len(rows) == N_FUTURE * len(MODEL_FEATURES)
    assert all(r[2] is not None and r[2] > 0 for r in rows)
    played = mart_con.execute(
        "SELECT count(*) FROM predictions WHERE actual IS NOT NULL AND match_id IN "
        "(SELECT match_id FROM mart_match_features WHERE is_played)"
    ).fetchone()
    assert played is not None
    total = mart_con.execute("SELECT count(*) FROM predictions").fetchone()
    assert total is not None
    assert played[0] == total[0] - len(rows)


def test_model_variance_has_one_row_per_model_per_seed(
    mart_con: duckdb.DuckDBPyConnection,
) -> None:
    """The noise floor: MAE per seed, and the first seed's row is the
    model_metrics number."""
    run_date = dt.date(2024, 9, 3)
    summary = train_all(mart_con, run_date)
    rows = mart_con.execute(
        "SELECT model_name, seed, mae FROM model_variance ORDER BY model_name, seed"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == sorted(
        (name, seed) for name in MODEL_FEATURES for seed in config.VARIANCE_SEEDS
    )
    primary = config.VARIANCE_SEEDS[0]
    for name in MODEL_FEATURES:
        first_seed = next(r[2] for r in rows if r[0] == name and r[1] == primary)
        assert first_seed == pytest.approx(summary["mae"][name])
        assert set(summary["variance"][name]) == set(config.VARIANCE_SEEDS)

    explicit = train_all(mart_con, run_date, seeds=[3, 5, 8])
    seeds = {
        r[0]
        for r in mart_con.execute(
            "SELECT DISTINCT seed FROM model_variance WHERE run_date = ?", [run_date]
        ).fetchall()
    }
    assert seeds == {42, 7, 3, 5, 8}
    assert explicit["seeds"] == (3, 5, 8)


def test_model_cv_has_one_fold_per_later_season_for_all_three_models(
    mart_con: duckdb.DuckDBPyConnection,
) -> None:
    """Two seasons: one fold, season 2024 held out, trained on 2023, for the two
    models and the naive baseline. One season: no folds, and no error."""
    train_all(mart_con, dt.date(2024, 9, 3))
    rows = mart_con.execute(
        "SELECT model_name, fold_season, n_train, n_test, mae FROM model_cv ORDER BY model_name"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(name, 2024) for name in sorted(ALL_MODEL_NAMES)]
    played = dict(
        mart_con.execute(
            "SELECT season, count(*) FROM mart_match_features WHERE is_played GROUP BY season"
        ).fetchall()
    )
    assert {(r[2], r[3]) for r in rows} == {(played[2023], played[2024])}
    assert all(r[4] > 0 for r in rows)

    one_season = synthetic_mart()
    one_season = one_season[one_season["season"] == 2023].reset_index(drop=True)
    single = duckdb.connect(":memory:")
    try:
        write_mart(single, one_season)
        summary = train_all(single, dt.date(2023, 9, 5))
        assert counts(single)["model_cv"] == 0
        assert counts(single)["model_metrics"] == len(ALL_MODEL_NAMES)
    finally:
        single.close()
    assert summary["cv"] == {}


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------


def test_export_all_writes_existing_tables_and_the_band(
    mart_con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """One CSV per table in EXTRACT_TABLES that exists, none for tables that do
    not, and predictions_with_band.csv with the band around the prediction."""
    train_all(mart_con, dt.date(2024, 9, 3))
    paths = export_all(mart_con, tmp_path)

    present = {t for t in config.EXTRACT_TABLES if t in (*OUTPUT_TABLES, "mart_match_features")}
    absent = set(config.EXTRACT_TABLES) - present
    assert absent, "the synthetic database is meant to be missing some extract tables"
    assert {p.name for p in paths} == {f"{t}.csv" for t in present} | {f"{BAND_FILE_STEM}.csv"}
    assert all(p.parent == tmp_path and p.exists() for p in paths)
    assert not any((tmp_path / f"{t}.csv").exists() for t in absent)

    band = pd.read_csv(tmp_path / f"{BAND_FILE_STEM}.csv")
    assert list(band.columns) == [
        "match_id",
        "model_name",
        "run_date",
        "predicted",
        "actual",
        "mae",
        "band_low",
        "band_high",
        "band_label",
    ]
    total = mart_con.execute("SELECT count(*) FROM predictions").fetchone()
    assert total is not None and len(band) == total[0]
    assert set(band["model_name"]) == ALL_MODEL_NAMES
    assert (band["band_low"] < band["predicted"]).all()
    assert (band["predicted"] < band["band_high"]).all()
    assert np.allclose(band["band_high"] - band["predicted"], band["mae"])
    assert np.allclose(band["predicted"] - band["band_low"], band["mae"])
    assert (band["band_label"] == BAND_LABEL).all()
    assert band["actual"].isna().sum() == N_FUTURE * len(MODEL_FEATURES)
    assert band["run_date"].map(lambda v: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v))).all()


def test_export_csv_writes_dates_as_iso_text(
    mart_con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The mart's DATE column lands as YYYY-MM-DD, no time part, no index column."""
    path = export_csv(mart_con, "mart_match_features", tmp_path / "nested")
    assert path == tmp_path / "nested" / "mart_match_features.csv"
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(frame.columns) == list(mart_columns())
    assert frame["date"].map(lambda v: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v))).all()
    rows = mart_con.execute("SELECT count(*) FROM mart_match_features").fetchone()
    assert rows is not None and len(frame) == rows[0]


def test_export_hyper_without_pantab_names_the_csv(
    mart_con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing pantab raises a plain ImportError from export_hyper that points at
    the CSV, and export_all with hyper=True still writes every CSV."""
    monkeypatch.setitem(sys.modules, "pantab", None)  # import fails even if installed
    with pytest.raises(ImportError) as excinfo:
        export_hyper(mart_con, "mart_match_features", tmp_path)
    assert str(tmp_path / "mart_match_features.csv") in str(excinfo.value)
    assert not list(tmp_path.glob("*.hyper"))

    paths = export_all(mart_con, tmp_path, hyper=True)
    assert [p.suffix for p in paths] == [".csv"] * len(paths)
    assert (tmp_path / "mart_match_features.csv").exists()


def test_export_hyper_with_pantab_writes_beside_the_csv(
    mart_con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With pantab importable, hyper=True writes a .hyper for every exported table."""
    written: list[tuple[str, int]] = []

    def frame_to_hyper(frame: pd.DataFrame, path: Path, *, table: str) -> None:
        Path(path).write_bytes(b"hyper")
        written.append((table, len(frame)))

    fake = types.ModuleType("pantab")
    fake.frame_to_hyper = frame_to_hyper  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pantab", fake)

    path = export_hyper(mart_con, "mart_match_features", tmp_path)
    assert path == tmp_path / "mart_match_features.hyper" and path.exists()
    rows = mart_con.execute("SELECT count(*) FROM mart_match_features").fetchone()
    assert rows is not None and written == [("mart_match_features", rows[0])]

    paths = export_all(mart_con, tmp_path, hyper=True)
    csvs = {p.stem for p in paths if p.suffix == ".csv"} - {BAND_FILE_STEM}
    hypers = {p.stem for p in paths if p.suffix == ".hyper"}
    assert hypers == csvs
    assert (tmp_path / f"{BAND_FILE_STEM}.csv").exists() is False  # no model tables yet
