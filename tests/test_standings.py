"""Standings reconstruction: point-in-time correctness and tie-breaking.

The most important test file here. Point-in-time leakage does not raise - it
shows up as suspiciously good validation error, which is easy to mistake for
success.

Doc: docs/phases/04-standings-as-of-match-date.md
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest


def test_first_match_of_season_has_zero_points(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame
) -> None:
    """pts_before, gd_before, and played_before are all zero, not null.

    The window returns null for the first row of each partition, and a null rank
    propagates into the features. COALESCE it.
    """
    pytest.skip("TODO: materialise int_standings from tiny_season and assert")


def test_points_before_second_match_equals_first_match_result(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame
) -> None:
    """Club A won its first match, so pts_before on its second match is 3.

    This is the leakage test in its smallest form. If pts_before on match two
    already includes match two's result, the number is wrong in an obvious
    direction, and it is obvious only because this fixture is small enough to
    check by hand.
    """
    pytest.skip("TODO")


def test_no_row_uses_a_result_on_or_after_its_own_date(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame
) -> None:
    """The general form of the leakage test.

    Recompute pts_before independently from matches strictly before each row's
    date and compare. Any disagreement is a leak.
    """
    pytest.skip("TODO: this is transform.checks.no_future_leakage, tested directly")


def test_final_standings_match_hand_computed_table(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame
) -> None:
    """Final table from tiny_season, worked out on paper.

    After six matches: club_a has 7 points (W, W, D), club_b has 4 (L, D, W),
    club_c has 2 (D, D, D), club_d has 1 (D, L, L). Verify against the fixture
    before trusting this - the point of a hand-checkable fixture is that you
    check it by hand.
    """
    pytest.skip("TODO")


def test_ties_share_a_position(con: duckdb.DuckDBPyConnection) -> None:
    """RANK(), not ROW_NUMBER().

    Two clubs level on points, goal difference, and goals for must share a
    position rather than being ordered arbitrarily by whatever the engine felt
    like - otherwise rank jitters between runs on identical data.
    """
    pytest.skip("TODO")


def test_rank_is_within_conference_not_league_wide(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Two conferences, and each has its own club ranked 1.

    This project ranks within conference. A fixture with an Eastern and a Western
    club, each top of its own conference, should produce two rows with
    rank_before = 1 on the same date.
    """
    pytest.skip("TODO: extend tiny_season with a second conference")
