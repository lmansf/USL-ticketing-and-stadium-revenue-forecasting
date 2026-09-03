"""Club alias mapping - the silent failure mode.

Doc: docs/phases/03-club-name-consistency.md
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest


def test_unmapped_club_is_detected_and_named(con: duckdb.DuckDBPyConnection) -> None:
    """The check must name the exact unmapped string.

    Fixing an unmapped club should be a paste into club_aliases.csv, not an
    investigation. This is what makes demo scenario D3 work.
    """
    pytest.skip("TODO: exercise usl.transform.checks.all_clubs_mapped")


def test_all_mapped_passes(con: duckdb.DuckDBPyConnection, club_aliases: pd.DataFrame) -> None:
    """A fully mapped staging table passes."""
    pytest.skip("TODO")


def test_row_count_catches_what_null_check_does_not(con: duckdb.DuckDBPyConnection) -> None:
    """Two raw names pointing at one club_id produce no nulls at all.

    This is exactly why row_count_preserved exists as a second, independent
    signal. A mapping that collides two distinct clubs passes all_clubs_mapped
    cleanly and is still wrong.
    """
    pytest.skip("TODO: exercise usl.transform.checks.row_count_preserved")


def test_normalization_does_not_collide_distinct_clubs() -> None:
    """Whitespace and case normalisation only. Nothing more aggressive.

    A collision here is the silent failure this whole phase exists to prevent -
    strip 'FC' and two genuinely different clubs can merge. A near-miss you add
    to the CSV by hand costs thirty seconds; a collision costs a corrupted
    feature you may never find.
    """
    pytest.skip("TODO: assert that two similarly-named distinct clubs stay distinct")
