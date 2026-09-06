"""The hand-maintained USL rows in usl/ref/, checked as data.

The USL club-seasons were written before a USL match was archived, from the
league's published conference lists, so nothing upstream has validated them.
These tests pin what can be pinned without the data: the shape of every file,
that every club named in one file exists in the others, and the size of every
conference-season - so an accidental edit shows up here rather than as a moved
playoff line in a season nobody looked at.

What they cannot pin is whether a club is filed under the right conference.
That is checks.conference_membership_is_plausible's job, on the fixtures, once
the season is pulled.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from usl import config
from usl.transform import checks, runner
from usl.transform.reference import REFERENCE_CSVS, create_ref_config, read_reference_csv

# (season, conference) -> number of clubs. USL Championship, the league's own
# conference lists. 2021 was played in four divisions and the division is the
# conference here (see conference_structure.csv for why).
USL_CONFERENCE_SIZES: dict[tuple[int, str], int] = {
    (2017, "Eastern"): 15,
    (2017, "Western"): 15,
    (2018, "Eastern"): 16,
    (2018, "Western"): 17,
    (2019, "Eastern"): 18,
    (2019, "Western"): 18,
    (2020, "Eastern"): 17,
    (2020, "Western"): 18,
    (2021, "Atlantic"): 8,
    (2021, "Central"): 8,
    (2021, "Mountain"): 7,
    (2021, "Pacific"): 8,
    (2022, "Eastern"): 13,
    (2022, "Western"): 14,
    (2023, "Eastern"): 12,
    (2023, "Western"): 12,
    (2024, "Eastern"): 12,
    (2024, "Western"): 12,
    (2025, "Eastern"): 13,
    (2025, "Western"): 11,
}


@pytest.fixture(scope="module")
def ref() -> duckdb.DuckDBPyConnection:
    """The five reference CSVs loaded the way the runner loads them, plus stg_clubs."""
    con = duckdb.connect(":memory:")
    create_ref_config(con)
    for name, path in REFERENCE_CSVS.items():
        read_reference_csv(con, name, path)
    runner.materialise(con, "stg_clubs")
    return con


def _frame(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


def test_every_file_reads_as_its_declared_columns(ref: duckdb.DuckDBPyConnection) -> None:
    """A comma inside a note splits a row; the loader would read one wide column."""
    expected = {
        "club_aliases": ["raw_name", "club_id", "note"],
        "club_conference": ["club_id", "season", "conference", "display_name", "note"],
        "conference_structure": [
            "season",
            "conference",
            "playoff_spots",
            "relegation_spots",
            "note",
        ],
        "derbies": ["club_id_a", "club_id_b", "note"],
        "stadiums": ["club_id", "stadium", "lat", "lon", "valid_from", "valid_to", "note"],
    }
    for table, columns in expected.items():
        got = [r[0] for r in ref.execute(f'DESCRIBE "{table}"').fetchall()]
        assert got == columns, table
    for path in REFERENCE_CSVS.values():
        assert '"' not in path.read_text(encoding="utf-8"), f"{path.name}: a quoted field"


def test_usl_conference_seasons_have_the_published_sizes(ref: duckdb.DuckDBPyConnection) -> None:
    """Every USL conference-season is present with the right number of clubs, and no extra."""
    sizes = _frame(
        ref,
        """
        SELECT season, conference, count(*) AS n
        FROM stg_clubs WHERE conference <> 'Premier League'
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
    )
    got = {(int(r.season), r.conference): int(r.n) for r in sizes.itertuples()}
    assert got == USL_CONFERENCE_SIZES
    totals = {
        2017: 30,
        2018: 33,
        2019: 36,
        2020: 35,
        2021: 31,
        2022: 27,
        2023: 24,
        2024: 24,
        2025: 24,
    }
    by_season: dict[int, int] = {}
    for (season, _), n in got.items():
        by_season[season] = by_season.get(season, 0) + n
    assert by_season == totals


def test_club_conference_rows_are_complete_and_unique(ref: duckdb.DuckDBPyConnection) -> None:
    """No blank conference or display name, and one row per club-season."""
    blanks = _frame(
        ref,
        """
        SELECT club_id, season FROM club_conference
        WHERE conference IS NULL OR conference = '' OR display_name IS NULL OR display_name = ''
        """,
    )
    assert blanks.empty, blanks
    assert checks.one_conference_per_club_season(ref).passed


def test_every_club_in_a_conference_has_a_name_row(ref: duckdb.DuckDBPyConnection) -> None:
    """The proposal script maps a provider id through the club's name row, so each club needs one.

    Every display name in club_conference.csv is also a name row in
    club_aliases.csv, so a provider that renders the club by its name of the
    day still matches.
    """
    missing = _frame(
        ref,
        """
        SELECT DISTINCT c.club_id
        FROM club_conference c
        LEFT JOIN club_aliases a ON a.club_id = c.club_id
        WHERE a.club_id IS NULL
        """,
    )
    assert missing.empty, missing["club_id"].tolist()
    unnamed = _frame(
        ref,
        """
        SELECT DISTINCT c.club_id, c.display_name
        FROM club_conference c
        LEFT JOIN club_aliases a ON a.raw_name = c.display_name AND a.club_id = c.club_id
        WHERE a.club_id IS NULL
        """,
    )
    assert unnamed.empty, unnamed.to_dict("records")
    orphans = _frame(
        ref,
        """
        SELECT DISTINCT a.club_id FROM club_aliases a
        LEFT JOIN club_conference c ON c.club_id = a.club_id
        WHERE c.club_id IS NULL
        """,
    )
    assert orphans.empty, orphans["club_id"].tolist()


def test_alias_names_map_to_one_club_each(ref: duckdb.DuckDBPyConnection) -> None:
    """The same raw_name under two club_ids fans the staging join out."""
    dup = _frame(
        ref,
        "SELECT raw_name, count(DISTINCT club_id) AS n FROM club_aliases GROUP BY 1 HAVING n > 1",
    )
    assert dup.empty, dup.to_dict("records")


def test_conference_structure_covers_every_conference_season(
    ref: duckdb.DuckDBPyConnection,
) -> None:
    """One structure row per (season, conference) in club_conference, and it parses."""
    uncovered = _frame(
        ref,
        """
        SELECT DISTINCT c.season, c.conference
        FROM club_conference c
        LEFT JOIN conference_structure s ON s.season = c.season AND s.conference = c.conference
        WHERE s.season IS NULL
        """,
    )
    assert uncovered.empty, uncovered.to_dict("records")
    stray = _frame(
        ref,
        """
        SELECT DISTINCT s.season, s.conference
        FROM conference_structure s
        LEFT JOIN club_conference c ON s.season = c.season AND s.conference = c.conference
        WHERE c.season IS NULL
        """,
    )
    assert stray.empty, stray.to_dict("records")
    result = checks.conference_structure_is_well_formed(ref)
    assert result.passed, result.metadata
    usl = _frame(
        ref,
        "SELECT relegation_spots FROM conference_structure WHERE conference <> 'Premier League'",
    )
    assert usl["relegation_spots"].isna().all() or (usl["relegation_spots"] == "").all()
    assert config.ASSUMED_RELEGATION_SPOTS == 2


def test_derby_clubs_exist(ref: duckdb.DuckDBPyConnection) -> None:
    result = checks.derby_clubs_are_known(ref)
    assert result.passed, result.metadata
