"""Shared test fixtures.

Small, hand-built frames rather than samples of real data. A fixture you can
verify by reading it is worth more than a realistic one you cannot.
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb
import pandas as pd
import pytest


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    """An in-memory DuckDB connection, fresh per test."""
    connection = duckdb.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def tiny_season() -> pd.DataFrame:
    """A four-club, six-match season with a hand-checkable final table.

    Deliberately small enough to work out the expected standings on paper. Club A
    wins twice and draws once; club D loses everything. The point of the fixture
    is that the right answer is obvious by inspection, so a failing standings
    test points at the code rather than at the fixture.

    Columns: match_id, season, date, home_club_id, away_club_id, conference,
    home_goals, away_goals, attendance.
    """
    return pd.DataFrame(
        [
            # match_id, season, date, home, away, conf, hg, ag, attendance
            ("m1", 2024, "2024-03-02", "club_a", "club_b", "East", 2, 0, 5000),
            ("m2", 2024, "2024-03-02", "club_c", "club_d", "East", 1, 1, 4000),
            ("m3", 2024, "2024-03-09", "club_b", "club_c", "East", 0, 0, 4500),
            ("m4", 2024, "2024-03-09", "club_d", "club_a", "East", 1, 3, 3000),
            ("m5", 2024, "2024-03-16", "club_a", "club_c", "East", 1, 1, 5500),
            ("m6", 2024, "2024-03-16", "club_b", "club_d", "East", 2, 1, 4200),
        ],
        columns=[
            "match_id",
            "season",
            "date",
            "home_club_id",
            "away_club_id",
            "conference",
            "home_goals",
            "away_goals",
            "attendance",
        ],
    )


@pytest.fixture
def club_aliases() -> pd.DataFrame:
    """A minimal alias table covering the tiny_season clubs plus one alias."""
    return pd.DataFrame(
        [
            ("Club A", "club_a", ""),
            ("Club A FC", "club_a", "rebrand 2023"),
            ("Club B", "club_b", ""),
            ("Club C", "club_c", ""),
            ("Club D", "club_d", ""),
        ],
        columns=["raw_name", "club_id", "note"],
    )
