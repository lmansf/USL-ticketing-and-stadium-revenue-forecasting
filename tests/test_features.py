"""Feature construction and the definitions/columns contract.

Doc: docs/phases/06-features.md
"""

from __future__ import annotations

import duckdb
import pytest

from usl.features.definitions import (
    EVIDENCE,
    MODEL_FEATURES,
    PROREL_FEATURES,
    all_features,
    is_prorel,
)


def test_every_feature_has_an_evidence_classification() -> None:
    """No feature may be added to a family without being classified.

    The classification is data rather than prose precisely so it cannot drift
    away from the feature list. This test is what enforces that.
    """
    missing = set(all_features()) - set(EVIDENCE)
    assert not missing, f"features with no Evidence classification: {sorted(missing)}"


def test_no_orphan_classifications() -> None:
    """The other direction: nothing classified that is not a feature."""
    orphans = set(EVIDENCE) - set(all_features())
    assert not orphans, f"classified but not in any family: {sorted(orphans)}"


def test_prorel_model_is_a_strict_superset_of_baseline() -> None:
    """The two models differ only by the pro-rel family.

    If they differed any other way, a difference in error would not be
    attributable to the pro-rel features - which is the whole experiment.
    """
    base = set(MODEL_FEATURES["baseline"])
    prorel = set(MODEL_FEATURES["prorel"])
    assert base < prorel
    assert prorel - base == set(PROREL_FEATURES)


def test_is_prorel_agrees_with_the_family_list() -> None:
    """The flag driving the Tableau colour split matches the model definition."""
    for feature in all_features():
        assert is_prorel(feature) == (feature in PROREL_FEATURES)


def test_mart_columns_match_definitions(con: duckdb.DuckDBPyConnection) -> None:
    """Every defined feature exists as a mart column, and vice versa.

    Both directions. A feature defined but not built fails training with a
    KeyError; a column built but not defined is dead weight nobody removes.
    """
    pytest.skip("TODO: materialise the mart and compare its columns to all_features()")


def test_lag_window_excludes_the_current_match(con: duckdb.DuckDBPyConnection) -> None:
    """home_gate_ma3 must not include the match it is a feature of.

    ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING, not AND CURRENT ROW - and note that
    CURRENT ROW is also SQL's default frame when you write none. Build a fixture
    where the current match has a wildly different gate from the previous three
    and assert the average is unmoved by it.
    """
    pytest.skip("TODO")


def test_first_home_match_lag_is_null_and_that_is_allowed(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Some nulls are correct.

    A club's first ever home match has no last_home_gate. config.ALLOWED_NULL_FEATURES
    is where that decision is encoded, and features_not_null must respect it.
    """
    pytest.skip("TODO")


def test_covid_flag_covers_the_configured_range(con: duckdb.DuckDBPyConnection) -> None:
    """is_covid_affected matches config.COVID_START and COVID_END.

    The range is a judgement call, so the test pins the flag to the config rather
    than to specific dates - changing the range should not break the test.
    """
    pytest.skip("TODO")
