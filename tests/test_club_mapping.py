"""Club alias mapping - the silent failure mode.

These tests go through raw_matches and the real stg_clubs.sql / stg_matches.sql
rather than the conftest shortcut, because the join under test lives in that
SQL. raw_matches is built directly from the tiny_raw fixture so nothing here
depends on the loader.

Doc: docs/phases/03-club-name-consistency.md
"""

from __future__ import annotations

import duckdb
import pandas as pd

from usl.load.raw import RAW_COLUMNS, ensure_raw_tables
from usl.transform import runner
from usl.transform.checks import (
    all_clubs_mapped,
    one_match_per_club_per_date,
    row_count_preserved,
)
from usl.transform.reference import (
    create_ref_config,
    normalize_club_key,
    register_reference_frame,
)


def stage_from_raw(
    con: duckdb.DuckDBPyConnection,
    raw: pd.DataFrame,
    aliases: pd.DataFrame,
    club_rows: pd.DataFrame,
) -> None:
    """Load raw_matches from a frame, register the reference tables, build staging."""
    ensure_raw_tables(con)
    con.register("raw_frame", raw)
    con.execute(f"INSERT INTO raw_matches SELECT {', '.join(RAW_COLUMNS)} FROM raw_frame")
    con.unregister("raw_frame")
    register_reference_frame(con, "club_aliases", aliases)
    register_reference_frame(con, "club_conference", club_rows)
    structure = (
        club_rows[["season", "conference"]]
        .drop_duplicates()
        .assign(playoff_spots=2, relegation_spots=1, note="test structure")
    )
    register_reference_frame(con, "conference_structure", structure)
    register_reference_frame(
        con, "derbies", pd.DataFrame(columns=["club_id_a", "club_id_b", "note"])
    )
    create_ref_config(con)
    runner.materialise(con, "stg_clubs")
    runner.materialise(con, "stg_matches")


def rename_raw(raw: pd.DataFrame, old: str, new: str) -> pd.DataFrame:
    """tiny_raw with one raw club string replaced on both sides."""
    out = raw.copy()
    out.loc[out["home_raw"] == old, "home_raw"] = new
    out.loc[out["away_raw"] == old, "away_raw"] = new
    return out


def club_ids(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[str | None, str | None]]:
    """match_id -> (home_club_id, away_club_id) from staging."""
    rows = con.execute("SELECT match_id, home_club_id, away_club_id FROM stg_matches").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def test_unmapped_club_is_detected_and_named(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """The check must name the exact unmapped string.

    Fixing an unmapped club should be a paste into club_aliases.csv, not an
    investigation. This is what makes demo scenario D3 work. And the rows are
    still there - a LEFT JOIN keeps them with a null id so the count is intact
    and the check can see them; an inner join would have dropped three matches
    in silence.
    """
    stage_from_raw(con, rename_raw(tiny_raw, "Club C", "Club C United"), club_aliases, tiny_clubs)
    result = all_clubs_mapped(con)
    assert not result.passed
    assert result.tier == "staging"
    assert result.metadata["unmapped"] == ["Club C United"]
    assert result.metadata["n_unmapped"] == 1
    assert "club_aliases.csv" in result.metadata["hint"]
    assert "type mismatch" in result.metadata["hint"]
    ids = club_ids(con)
    assert len(ids) == 6
    assert [m for m, (h, a) in ids.items() if h is None or a is None] == ["m2", "m3", "m5"]
    assert row_count_preserved(con).passed  # nothing was dropped


def test_all_mapped_passes(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """A fully mapped staging table passes, including through a rebrand alias.

    'Club A FC' is a second raw_name for club_a. A club that rebrands keeps its
    club_id and gains a row; its history stays joined.
    """
    raw = tiny_raw.copy()
    raw.loc[raw["match_id"] == "m1", "home_raw"] = "Club A FC"
    stage_from_raw(con, raw, club_aliases, tiny_clubs)
    result = all_clubs_mapped(con)
    assert result.passed, result.metadata
    assert result.metadata == {"n_unmapped": 0, "unmapped": []}
    ids = club_ids(con)
    assert all(h is not None and a is not None for h, a in ids.values())
    assert ids["m1"] == ("club_a", "club_b")
    assert row_count_preserved(con).passed


def test_row_count_catches_what_null_check_does_not(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """A duplicated alias row produces no nulls at all - it fans the join out.

    This is exactly why row_count_preserved exists as a second, independent
    signal. 'Club C' listed twice (a botched CSV edit) maps every one of its
    three matches twice: all_clubs_mapped passes cleanly, and staging has nine
    rows from six.
    """
    doubled = pd.concat(
        [
            club_aliases,
            pd.DataFrame(
                [("Club C", "club_c_old", "left in by mistake")], columns=club_aliases.columns
            ),
        ],
        ignore_index=True,
    )
    stage_from_raw(con, tiny_raw, doubled, tiny_clubs)
    assert all_clubs_mapped(con).passed
    result = row_count_preserved(con)
    assert not result.passed
    assert result.metadata == {"raw_rows": 6, "staging_rows": 9, "difference": 3}


def test_colliding_two_clubs_onto_one_id_fires_the_club_date_check(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """Two raw names pointing at one club_id: no nulls, no row-count change.

    Mapping 'Club D' to club_c collides two distinct clubs. all_clubs_mapped
    passes (no nulls) and so does row_count_preserved (the join is still one
    to one), which is the guide's warning made concrete. The check that fires
    is one_match_per_club_per_date: club_c now plays itself in m2, and appears
    twice on every date.
    """
    collided = club_aliases.copy()
    collided.loc[collided["raw_name"] == "Club D", "club_id"] = "club_c"
    stage_from_raw(con, tiny_raw, collided, tiny_clubs)
    assert all_clubs_mapped(con).passed
    assert row_count_preserved(con).passed
    assert club_ids(con)["m2"] == ("club_c", "club_c")
    result = one_match_per_club_per_date(con)
    assert not result.passed
    assert result.metadata["n_club_dates"] == 3
    assert result.metadata["club_dates"][0] == {
        "season": 2024,
        "club_id": "club_c",
        "date": "2024-03-02",
        "matches": 2,
    }


def test_normalization_does_not_collide_distinct_clubs(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """Whitespace normalisation only. Nothing more aggressive.

    'Club  A ' (double space, trailing space) is club_a; 'Club A United' is a
    different club and must come back unmapped rather than being folded into
    club_a. A collision here is the silent failure this whole phase exists to
    prevent - strip 'FC' and two genuinely different clubs can merge. A
    near-miss you add to the CSV by hand costs thirty seconds; a collision
    costs a corrupted feature you may never find.
    """
    assert normalize_club_key("Tampa  Bay Rowdies ") == "Tampa Bay Rowdies"
    assert normalize_club_key(93) == normalize_club_key("93") == normalize_club_key(" 93 ")
    assert normalize_club_key(93.0) == "93"
    assert normalize_club_key("Club A FC") != normalize_club_key("Club A")
    assert normalize_club_key("Club A United") != normalize_club_key("Club A")
    assert normalize_club_key("club a") != normalize_club_key("Club A")  # case is kept

    raw = tiny_raw.copy()
    raw.loc[raw["match_id"] == "m1", "home_raw"] = "Club  A "
    raw.loc[raw["match_id"] == "m2", "home_raw"] = "Club A United"
    stage_from_raw(con, raw, club_aliases, tiny_clubs)
    ids = club_ids(con)
    assert ids["m1"] == ("club_a", "club_b")  # whitespace differences merge
    assert ids["m2"] == (None, "club_d")  # a distinct club does not
    result = all_clubs_mapped(con)
    assert not result.passed
    assert result.metadata["unmapped"] == ["Club A United"]


def test_provider_ids_and_display_names_both_map(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """raw_name holds provider ids as text as well as display names.

    The API sends club ids as numbers; the CSV holds them as strings. Both
    sides of the join are normalised through the same rule, so '93', ' 93 '
    and the display name all land on the same club_id.
    """
    aliases = pd.concat(
        [
            club_aliases,
            pd.DataFrame([("93", "club_a", "FootyStats id")], columns=club_aliases.columns),
        ],
        ignore_index=True,
    )
    raw = tiny_raw.copy()
    raw.loc[raw["match_id"] == "m1", "home_raw"] = "93"
    raw.loc[raw["match_id"] == "m5", "home_raw"] = " 93 "
    stage_from_raw(con, raw, aliases, tiny_clubs)
    assert all_clubs_mapped(con).passed
    ids = club_ids(con)
    assert ids["m1"][0] == "club_a"
    assert ids["m5"][0] == "club_a"
    stg_clubs = con.execute(
        "SELECT club_id, season, conference FROM stg_clubs ORDER BY 1"
    ).fetchall()
    assert stg_clubs == [
        ("club_a", 2024, "East"),
        ("club_b", 2024, "East"),
        ("club_c", 2024, "East"),
        ("club_d", 2024, "East"),
    ]
