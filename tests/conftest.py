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
    wins twice and draws once; club C draws all three; club D draws then loses
    twice. The point of the fixture is that the right answer is checkable by
    hand, so a failing standings test points at the code rather than at the
    fixture.

    Final table after all six matches, computed from the rows below:

        club_a  7 pts  (W, W, D)  gd +4  gf 6
        club_b  4 pts  (L, D, W)  gd -1  gf 2
        club_c  3 pts  (D, D, D)  gd  0  gf 2
        club_d  1 pt   (D, L, L)  gd -3  gf 3

    Note there is no conference column. Conference is an attribute of the
    club-season, not of a match - an interconference fixture has no single
    correct value - so it lives in the tiny_clubs fixture instead, mirroring
    stg_matches and stg_clubs.

    Columns: match_id, season, date, home_club_id, away_club_id, home_goals,
    away_goals, attendance.
    """
    return pd.DataFrame(
        [
            # match_id, season, date, home, away, hg, ag, attendance
            ("m1", 2024, "2024-03-02", "club_a", "club_b", 2, 0, 5000),
            ("m2", 2024, "2024-03-02", "club_c", "club_d", 1, 1, 4000),
            ("m3", 2024, "2024-03-09", "club_b", "club_c", 0, 0, 4500),
            ("m4", 2024, "2024-03-09", "club_d", "club_a", 1, 3, 3000),
            ("m5", 2024, "2024-03-16", "club_a", "club_c", 1, 1, 5500),
            ("m6", 2024, "2024-03-16", "club_b", "club_d", 2, 1, 4200),
        ],
        columns=[
            "match_id",
            "season",
            "date",
            "home_club_id",
            "away_club_id",
            "home_goals",
            "away_goals",
            "attendance",
        ],
    )


@pytest.fixture
def tiny_clubs() -> pd.DataFrame:
    """Club-season rows for tiny_season, in the shape of stg_clubs.

    All four clubs sit in one conference, so the hand-computed final table in
    test_standings applies directly. Add a second conference here when you write
    test_rank_is_within_conference_not_league_wide - that test needs two clubs
    each ranked 1 on the same date.
    """
    return pd.DataFrame(
        [
            ("club_a", 2024, "East", "Club A"),
            ("club_b", 2024, "East", "Club B"),
            ("club_c", 2024, "East", "Club C"),
            ("club_d", 2024, "East", "Club D"),
        ],
        columns=["club_id", "season", "conference", "display_name"],
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
