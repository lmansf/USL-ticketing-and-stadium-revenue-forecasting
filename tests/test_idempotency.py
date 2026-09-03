"""Idempotency and duplicate rejection.

This behaviour ships working. It is demonstrated as correct in phase 09, not
staged as a deliberate failure to be fixed on camera.

Doc: docs/phases/01-scrape-to-raw.md, exercise 1.2
     docs/phases/09-break-and-fix.md, "Demonstrate working, do not break"
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest


def test_second_load_inserts_nothing(con: duckdb.DuckDBPyConnection) -> None:
    """Loading the same frame twice reports zero inserted and N updated.

    This is the log line the demo points at.
    """
    pytest.skip("TODO: load tiny_season twice and assert on the LoadStats split")


def test_attendance_total_unchanged_by_a_second_load(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame
) -> None:
    """The stronger assertion.

    Row count alone is unchanged by a bug that overwrites every row with
    garbage. Identical sums plus zero inserts is hard to argue with.
    """
    pytest.skip("TODO")


def test_corrected_attendance_overwrites_the_original(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Upsert, not insert-ignore.

    Sources correct attendance after the fact, so the latest figure wins. A load
    strategy that ignores conflicts would pin the first, wrong number forever.
    """
    pytest.skip("TODO")


def test_duplicate_match_id_in_one_batch_is_rejected(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The primary key holds even within a single load.

    A source page that lists a match twice should not produce two rows.
    """
    pytest.skip("TODO")
