"""SQL layer: ordering and rebuild idempotency.

Doc: docs/phases/05-sql-layer.md
"""

from __future__ import annotations

import duckdb
import pytest

from usl.transform.runner import MODELS


def test_models_are_declared_in_dependency_order() -> None:
    """Staging before intermediate before mart.

    An explicit list rather than an inferred graph, so a reader can check it -
    and so this test can.
    """
    assert MODELS.index("stg_matches") < MODELS.index("int_standings")
    assert MODELS.index("int_standings") < MODELS.index("mart_match_features")


def test_every_model_has_a_sql_file() -> None:
    """A name in MODELS with no file fails at run time, in the middle of a run."""
    from usl.config import SQL_DIR

    missing = [m for m in MODELS if not (SQL_DIR / f"{m}.sql").exists()]
    assert not missing, f"declared models with no .sql file: {missing}"


def test_rerunning_the_layer_produces_identical_tables(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """CREATE OR REPLACE means a full rebuild, so twice equals once.

    Note this is the opposite of raw_matches, which upserts because it must not
    lose history the source no longer serves. Raw accumulates; everything below
    it is derived and disposable.
    """
    pytest.skip("TODO")


def test_failing_check_stops_before_the_next_tier(con: duckdb.DuckDBPyConnection) -> None:
    """Collect within a tier, stop between tiers.

    There is no value in computing a mart on staging you already know is broken -
    it just produces a second wave of failures that are downstream artefacts of
    the first.
    """
    pytest.skip("TODO")


def test_all_check_results_are_logged_not_only_failures(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A check that only writes a row when it fails gives you no baseline."""
    pytest.skip("TODO")
