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
from conftest import stage_frames

from usl.config import SQL_DIR
from usl.load.raw import RAW_COLUMNS, ensure_raw_tables
from usl.transform import runner
from usl.transform.checks import (
    all_clubs_mapped,
    one_match_per_club_per_date,
    row_count_preserved,
)
from usl.transform.reference import (
    NORMALIZE_SQL,
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


def test_staging_join_uses_the_shared_normalisation_expression() -> None:
    """stg_matches.sql normalises the raw side with reference.NORMALIZE_SQL, verbatim.

    The SQL file is static, so the expression is copied into it rather than
    formatted in. This pins the copy to the Python constant: if the rule
    changes on one side only, the join silently drifts from the loader that
    normalised the CSV, and that drift is the 93 versus "93" trap in a new
    coat. Both join keys must use it.
    """
    sql = (SQL_DIR / "stg_matches.sql").read_text(encoding="utf-8")
    for col in ("p.home_raw", "p.away_raw"):
        expression = NORMALIZE_SQL.format(col=col)
        assert sql.count(expression) == 1, f"{expression} not used exactly once for {col}"


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


def test_duplicate_club_season_is_named_before_it_can_fan_out(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A (club_id, season) listed twice in club_conference.csv produces no null.

    It fans the standings grid out instead - the club sits in the table twice
    and n_clubs is inflated, which shifts the playoff and relegation lines - and
    doubles that club's matches in the mart. Neither the alias check nor the
    conference check sees it. one_conference_per_club_season names the pair at
    the staging tier, and this test shows the inflation it would otherwise
    cause two tiers down.
    """
    from usl.transform.checks import one_conference_per_club_season
    from usl.transform.runner import materialise

    duplicated = pd.concat([tiny_clubs, tiny_clubs.iloc[[0]]], ignore_index=True)
    stage_frames(con, tiny_season, duplicated)

    result = one_conference_per_club_season(con)
    assert not result.passed
    assert result.metadata["duplicated"] == [
        {"club_id": "club_a", "season": 2024, "rows": 2, "conferences": ["East"]}
    ]
    assert "club_conference.csv" in result.metadata["hint"]

    # The silent damage the check exists to prevent: five "clubs" in a four-club table.
    materialise(con, "int_standings")
    row = con.execute("SELECT max(n_clubs) FROM int_standings").fetchone()
    assert row is not None and row[0] == 5


def test_unique_club_seasons_pass(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """One row per club-season is the normal case and passes with an empty list."""
    from usl.transform.checks import one_conference_per_club_season

    stage_frames(con, tiny_season, tiny_clubs)
    result = one_conference_per_club_season(con)
    assert result.passed
    assert result.metadata == {"n_duplicated": 0, "duplicated": []}


# ---------------------------------------------------------------------------
# Reference data that is wrong without being null
# ---------------------------------------------------------------------------


def _stage_from_frames(
    con: duckdb.DuckDBPyConnection,
    raw: pd.DataFrame,
    aliases: pd.DataFrame,
    club_rows: pd.DataFrame,
) -> None:
    """raw_matches plus the reference tables, then the two staging models."""
    ensure_raw_tables(con)
    con.register("_raw", raw)
    con.execute(f"INSERT INTO raw_matches SELECT {', '.join(RAW_COLUMNS)} FROM _raw")
    con.unregister("_raw")
    register_reference_frame(con, "club_aliases", aliases)
    register_reference_frame(con, "club_conference", club_rows)
    register_reference_frame(
        con,
        "conference_structure",
        club_rows[["season", "conference"]]
        .drop_duplicates()
        .assign(playoff_spots=2, relegation_spots=1, note=""),
    )
    register_reference_frame(
        con, "derbies", pd.DataFrame(columns=["club_id_a", "club_id_b", "note"])
    )
    create_ref_config(con)
    runner.materialise(con, "stg_clubs")
    runner.materialise(con, "stg_matches")


def test_blank_conference_counts_as_missing(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A club_conference.csv row with an empty conference cell would vanish from the grid.

    NULL never equals NULL, so the club gets no standings rows and the failure
    would surface two tiers later as null features with no club named. The
    staging check names it.
    """
    from usl.transform.checks import all_club_seasons_have_conference

    blank = tiny_clubs.copy()
    blank.loc[blank["club_id"] == "club_d", "conference"] = None
    stage_frames(con, tiny_season, blank)

    result = all_club_seasons_have_conference(con)
    assert not result.passed
    assert result.metadata["missing"] == [{"club_id": "club_d", "season": 2024}]
    assert "blank conference" in result.metadata["hint"]


def test_phantom_club_season_is_named_before_it_moves_the_line(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A club listed for a season it plays no fixture in sits in the table on 0 points.

    n_clubs is one too many and the relegation line moves a place; no null
    appears anywhere. all_conference_clubs_have_fixtures names the pair.
    """
    from usl.transform.checks import all_conference_clubs_have_fixtures
    from usl.transform.runner import materialise

    phantom = pd.concat(
        [
            tiny_clubs,
            pd.DataFrame([("club_e", 2024, "East", "Club E")], columns=tiny_clubs.columns),
        ],
        ignore_index=True,
    )
    stage_frames(con, tiny_season, phantom)

    result = all_conference_clubs_have_fixtures(con)
    assert not result.passed
    assert result.metadata["without_fixtures"] == [
        {"club_id": "club_e", "season": 2024, "conference": "East"}
    ]
    assert "club_conference.csv" in result.metadata["hint"]

    # the silent damage the check prevents
    materialise(con, "int_standings")
    row = con.execute("SELECT max(n_clubs) FROM int_standings").fetchone()
    assert row is not None and row[0] == 5

    stage_frames(con, tiny_season, tiny_clubs)
    assert all_conference_clubs_have_fixtures(con).passed


def test_conference_structure_problems_are_named(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """Duplicated pairs, unparseable spots, and spots beyond the conference size all fail."""
    from usl.transform.checks import conference_structure_is_well_formed

    columns = ["season", "conference", "playoff_spots", "relegation_spots", "note"]
    good = pd.DataFrame([(2024, "East", 2, 1, "")], columns=columns)
    stage_frames(con, tiny_season, tiny_clubs, structure=good)
    assert conference_structure_is_well_formed(con).passed

    duplicated = pd.DataFrame(
        [(2024, "East", 2, 1, ""), (2024, "East", 2, 1, "again")], columns=columns
    )
    stage_frames(con, tiny_season, tiny_clubs, structure=duplicated)
    result = conference_structure_is_well_formed(con)
    assert not result.passed
    assert result.metadata["duplicated"] == [{"season": "2024", "conference": "East", "rows": 2}]

    typo = pd.DataFrame([(2024, "East", 2, "one", "")], columns=columns)
    stage_frames(con, tiny_season, tiny_clubs, structure=typo)
    result = conference_structure_is_well_formed(con)
    assert not result.passed
    assert result.metadata["unparseable"][0]["relegation_spots"] == "one"

    too_many = pd.DataFrame([(2024, "East", 4, 1, "")], columns=columns)  # four clubs
    stage_frames(con, tiny_season, tiny_clubs, structure=too_many)
    result = conference_structure_is_well_formed(con)
    assert not result.passed
    assert result.metadata["out_of_range"][0]["n_clubs"] == 4


def test_unknown_derby_club_is_named(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A typo in derbies.csv would silently make is_derby false for every meeting."""
    from usl.transform.checks import derby_clubs_are_known

    typo = pd.DataFrame([("club_a", "club_bb", "typo")], columns=["club_id_a", "club_id_b", "note"])
    stage_frames(con, tiny_season, tiny_clubs, derbies=typo)
    result = derby_clubs_are_known(con)
    assert not result.passed
    assert result.metadata["unknown"] == ["club_bb"]

    fine = pd.DataFrame([("club_a", "club_b", "")], columns=["club_id_a", "club_id_b", "note"])
    stage_frames(con, tiny_season, tiny_clubs, derbies=fine)
    assert derby_clubs_are_known(con).passed


def test_alias_row_with_a_blank_club_id_gets_the_fill_in_hint(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """The string IS in the CSV; the fix is to fill the club_id in, not to add a row."""
    from usl.transform.checks import all_clubs_mapped

    aliases = club_aliases.copy()
    aliases.loc[aliases["raw_name"] == "Club C", "club_id"] = None
    _stage_from_frames(con, tiny_raw, aliases, tiny_clubs)

    result = all_clubs_mapped(con)
    assert not result.passed
    assert result.metadata["unmapped"] == ["Club C"]
    assert result.metadata["blank_club_id"] == ["Club C"]
    assert "fill the club_id in" in result.metadata["hint"]
    assert "add each string" not in result.metadata["hint"]


def test_status_drift_is_named_rather_than_un_playing_the_season(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """A renamed status must stop the run, not quietly strip every result.

    Case and whitespace drift are absorbed by staging ('Complete ' is played).
    A genuinely new value ('finished') on a row that plainly carries a result
    and a gate is both an unknown status and an inconsistency, and the check
    names it with its count.
    """
    from usl.transform.checks import played_rows_consistent

    raw = tiny_raw.copy()
    raw.loc[0, "status"] = "Complete "
    _stage_from_frames(con, raw, club_aliases, tiny_clubs)
    assert con.execute("SELECT is_played FROM stg_matches WHERE match_id = 'm1'").fetchone() == (
        True,
    )
    assert played_rows_consistent(con).passed

    con.execute("DROP TABLE raw_matches")
    raw.loc[1, "status"] = "finished"
    _stage_from_frames(con, raw, club_aliases, tiny_clubs)
    assert con.execute("SELECT is_played FROM stg_matches WHERE match_id = 'm2'").fetchone() == (
        False,
    )
    result = played_rows_consistent(con)
    assert not result.passed
    assert result.metadata["inconsistent_statuses"] == {"finished": 1}
    assert result.metadata["unknown_statuses"] == {"finished": 1}
    assert "KNOWN_MATCH_STATUSES" in result.metadata["hint"]


def test_reference_rows_ahead_of_the_data_are_not_phantoms(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A conference-season with no fixture at all is data not yet pulled, not a phantom.

    The USL rows sit in club_conference.csv before a USL match is archived.
    The standings grid takes its dates from fixtures, so such a conference has
    no rows and moves no line; the check reports it and passes. A club pasted
    into a conference that IS playing still fails.
    """
    from usl.transform.checks import all_conference_clubs_have_fixtures
    from usl.transform.runner import materialise

    ahead = pd.concat(
        [
            tiny_clubs,
            pd.DataFrame(
                [
                    ("club_x", 2025, "East", "Club X"),  # next season, nothing pulled
                    ("club_y", 2025, "East", "Club Y"),
                    ("club_w", 2024, "West", "Club W"),  # this season, other conference
                ],
                columns=tiny_clubs.columns,
            ),
        ],
        ignore_index=True,
    )
    stage_frames(con, tiny_season, ahead)
    result = all_conference_clubs_have_fixtures(con)
    assert result.passed
    assert result.metadata["conference_seasons_without_fixtures"] == [
        {"season": 2024, "conference": "West", "n_clubs": 1},
        {"season": 2025, "conference": "East", "n_clubs": 2},
    ]

    materialise(con, "int_standings")
    rows = con.execute(
        "SELECT conference, max(n_clubs), count(*) FROM int_standings GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert rows == [("East", 4, 16)]  # four clubs on three dates plus the snapshot; no West

    pasted = pd.concat(
        [ahead, pd.DataFrame([("club_e", 2024, "East", "Club E")], columns=tiny_clubs.columns)],
        ignore_index=True,
    )
    stage_frames(con, tiny_season, pasted)
    result = all_conference_clubs_have_fixtures(con)
    assert not result.passed
    assert [r["club_id"] for r in result.metadata["without_fixtures"]] == ["club_e"]


def test_club_filed_under_the_wrong_conference_is_named_by_its_schedule(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The one club_conference.csv error no other check can see.

    A club filed under the wrong conference is present, mapped, playing, and
    listed once. Its fixtures give it away: a conference is the set of clubs
    that mostly play each other. Four East clubs play a round robin, three
    West clubs play theirs, and one cross-conference match is thrown in so a
    balanced club is not confused with a misfiled one.
    """
    from usl.transform.checks import conference_membership_is_plausible

    columns = list(
        (
            "match_id",
            "season",
            "date",
            "home_club_id",
            "away_club_id",
            "home_goals",
            "away_goals",
            "attendance",
        )
    )
    matches = pd.DataFrame(
        [
            ("e1", 2024, "2024-03-02", "club_a", "club_b", 1, 0, 1000),
            ("e2", 2024, "2024-03-02", "club_c", "club_g", 1, 0, 1000),
            ("e3", 2024, "2024-03-09", "club_a", "club_c", 1, 0, 1000),
            ("e4", 2024, "2024-03-09", "club_b", "club_g", 1, 0, 1000),
            ("e5", 2024, "2024-03-16", "club_a", "club_g", 1, 0, 1000),
            ("e6", 2024, "2024-03-16", "club_b", "club_c", 1, 0, 1000),
            ("w1", 2024, "2024-03-02", "club_d", "club_e", 1, 0, 1000),
            ("w2", 2024, "2024-03-09", "club_d", "club_f", 1, 0, 1000),
            ("w3", 2024, "2024-03-16", "club_e", "club_f", 1, 0, 1000),
            ("x1", 2024, "2024-03-23", "club_a", "club_d", 1, 0, 1000),  # cross-conference
        ],
        columns=columns,
    )
    clubs = pd.DataFrame(
        [
            ("club_a", 2024, "East", "A"),
            ("club_b", 2024, "East", "B"),
            ("club_c", 2024, "East", "C"),
            ("club_g", 2024, "East", "G"),
            ("club_d", 2024, "West", "D"),
            ("club_e", 2024, "West", "E"),
            ("club_f", 2024, "West", "F"),
        ],
        columns=["club_id", "season", "conference", "display_name"],
    )
    stage_frames(con, matches, clubs)
    assert conference_membership_is_plausible(con).passed

    misfiled = clubs.copy()
    misfiled.loc[misfiled["club_id"] == "club_c", "conference"] = "West"
    stage_frames(con, matches, misfiled)
    result = conference_membership_is_plausible(con)
    assert not result.passed
    assert result.metadata["implausible"] == [
        {
            "season": 2024,
            "club_id": "club_c",
            "conference": "West",
            "fixtures_in_conference": 0,
            "fixtures_outside": 3,
        }
    ]
    assert "club_conference.csv" in result.metadata["hint"]

    # a void fixture is not evidence either way
    stage_frames(con, matches, misfiled, void=["e2", "e3", "e6"])
    assert conference_membership_is_plausible(con).passed
