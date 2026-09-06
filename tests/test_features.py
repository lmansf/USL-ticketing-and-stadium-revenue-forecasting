"""Feature construction and the definitions/columns contract.

Every fixture here is small enough to work out on paper, and the docstring of
each test says what the paper answer is. The elimination fixture at the bottom
of the helpers is the one to read first: it is the only place the playoff-line
arithmetic, matches_since_elimination, and the decay curve are checked against
numbers a person computed.

Doc: docs/phases/06-features.md
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd
import pytest
from conftest import stage_frames, with_unplayed

from usl import config
from usl.features.definitions import (
    EVIDENCE,
    MODEL_FEATURES,
    PROREL_FEATURES,
    all_features,
    is_prorel,
    mart_columns,
)
from usl.load.raw import RAW_COLUMNS, ensure_raw_tables
from usl.transform import runner
from usl.transform.checks import features_not_null, mart_matches_staging
from usl.transform.reference import create_ref_config, register_reference_frame

MATCH_COLUMNS = [
    "match_id",
    "season",
    "date",
    "home_club_id",
    "away_club_id",
    "home_goals",
    "away_goals",
    "attendance",
]
CLUB_COLUMNS = ["club_id", "season", "conference", "display_name"]
STRUCTURE_COLUMNS = ["season", "conference", "playoff_spots", "relegation_spots", "note"]
DOWNSTREAM_MODELS = (
    "stg_weather",
    "int_standings",
    "int_stakes",
    "mart_match_features",
    "mart_decay_curve",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def matches(rows: list[tuple[object, ...]]) -> pd.DataFrame:
    """tiny_season-shaped rows."""
    return pd.DataFrame(rows, columns=MATCH_COLUMNS)


def clubs(rows: list[tuple[object, ...]]) -> pd.DataFrame:
    """tiny_clubs-shaped rows."""
    return pd.DataFrame(rows, columns=CLUB_COLUMNS)


def build_mart(
    con: duckdb.DuckDBPyConnection,
    season: pd.DataFrame,
    club_rows: pd.DataFrame,
    *,
    structure: pd.DataFrame | None = None,
    derbies: pd.DataFrame | None = None,
) -> None:
    """Stage the frames and materialise everything downstream from the real SQL."""
    stage_frames(con, season, club_rows, structure=structure, derbies=derbies)
    for model in DOWNSTREAM_MODELS:
        runner.materialise(con, model)


def mart(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, object]]:
    """mart_match_features rows keyed by match_id, each a column -> value dict."""
    cols = [r[0] for r in con.execute("DESCRIBE mart_match_features").fetchall()]
    rows = con.execute("SELECT * FROM mart_match_features").fetchall()
    return {r[0]: dict(zip(cols, r, strict=True)) for r in rows}


def stage_from_raw(
    con: duckdb.DuckDBPyConnection,
    raw: pd.DataFrame,
    aliases: pd.DataFrame,
    club_rows: pd.DataFrame,
) -> None:
    """Raw to staging through the real stg_*.sql files (stage_frames skips raw)."""
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


# Five home matches for club_a, gates flat at 1000 until the fourth, which is
# wildly different. A lag that includes the current row moves on l4; a correct
# one moves only on l5.
LAG_MATCHES = [
    ("l1", 2024, "2024-03-02", "club_a", "club_b", 1, 0, 1000),
    ("l2", 2024, "2024-03-09", "club_a", "club_c", 1, 0, 1000),
    ("l3", 2024, "2024-03-16", "club_a", "club_d", 1, 0, 1000),
    ("l4", 2024, "2024-03-23", "club_a", "club_e", 1, 0, 99999),
    ("l5", 2024, "2024-03-30", "club_a", "club_b", 1, 0, 1000),
    # two more, with distinct gates, so the five-match window is pinned: a
    # four-row or six-row ma5 gives a different mean on l6 and l7
    ("l6", 2024, "2024-04-06", "club_a", "club_c", 1, 0, 2000),
    ("l7", 2024, "2024-04-13", "club_a", "club_d", 1, 0, 3000),
]
LAG_CLUBS = [(f"club_{c}", 2024, "East", f"Club {c.upper()}") for c in "abcde"]

# Two seasons of the same two clubs. club_a hosted club_b twice in 2023, so
# same_fixture_last_season for s4 must pick the more recent (s3, 5100), and
# club_a's first 2024 home match takes its lags from the end of 2023.
TWO_SEASON_MATCHES = [
    ("s1", 2023, "2023-03-04", "club_a", "club_b", 1, 0, 4800),
    ("s2", 2023, "2023-03-11", "club_b", "club_a", 0, 0, 3900),
    ("s3", 2023, "2023-03-18", "club_a", "club_b", 2, 1, 5100),
    ("s4", 2024, "2024-03-02", "club_a", "club_b", 0, 0, 5000),
    ("s5", 2024, "2024-03-09", "club_b", "club_a", 1, 0, 4000),
]
TWO_SEASON_CLUBS = [
    ("club_a", 2023, "East", "Club A"),
    ("club_b", 2023, "East", "Club B"),
    ("club_a", 2024, "East", "Club A"),
    ("club_b", 2024, "East", "Club B"),
]

# Two conferences with an interconference fixture (y5) in round two. After
# round one club_a leads the East on +3 and club_e leads the West on +1.
TWO_CONFERENCE_MATCHES = [
    ("y1", 2024, "2024-03-02", "club_a", "club_b", 3, 0, 5000),
    ("y2", 2024, "2024-03-02", "club_c", "club_d", 0, 0, 4000),
    ("y3", 2024, "2024-03-02", "club_e", "club_f", 1, 0, 6000),
    ("y4", 2024, "2024-03-02", "club_g", "club_h", 0, 0, 3500),
    ("y5", 2024, "2024-03-09", "club_a", "club_e", 1, 1, 5200),
    ("y6", 2024, "2024-03-09", "club_b", "club_f", 2, 0, 4100),
    ("y7", 2024, "2024-03-09", "club_c", "club_g", 0, 1, 3900),
    ("y8", 2024, "2024-03-09", "club_d", "club_h", 1, 1, 3000),
]
TWO_CONFERENCE_CLUBS = [(f"club_{c}", 2024, "East", f"Club {c.upper()}") for c in "abcd"] + [
    (f"club_{c}", 2024, "West", f"Club {c.upper()}") for c in "efgh"
]

# The elimination fixture: four clubs, five rounds, ONE playoff spot and one
# relegation spot. Worked on paper, "before" values as of each date:
#
#   cumulative table after each round (pts, gd, gf)
#     R1 03-02  a 3 +2 2 | b 0 -2 0 | c 1  0 1 | d 1  0 1
#     R2 03-09  a 6 +4 5 | b 1 -2 0 | c 2  0 1 | d 1 -2 2
#     R3 03-16  a 7 +4 6 | b 4 -1 2 | c 3  0 2 | d 1 -3 3
#     R4 03-23  a 10 +5 7 | b 4 -2 2 | c 4 0 2 | d 2 -3 3
#     R5 03-30  a 13 +7 9 | b 7 -1 3 | c 4 -1 2 | d 2 -5 3
#
#   every club has 5 fixtures. Live means pts_before + 3 * remaining > line,
#   where the line is the CURRENT points of the club in position 1:
#     03-23  line 7, 2 to play:  d 1 + 6 = 7, NOT > 7  -> d eliminated on 03-23
#     03-30  line 10, 1 to play: b 4 + 3 = 7, c 4 + 3 = 7 -> b, c eliminated on 03-30
#   so d's home matches: e8 (03-23) is 0 matches since elimination, e10 is 1;
#   b's e9 (03-30) is 0; every other home match is -1 (live).
ELIMINATION_MATCHES = [
    ("e1", 2024, "2024-03-02", "club_a", "club_b", 2, 0, 5000),
    ("e2", 2024, "2024-03-02", "club_c", "club_d", 1, 1, 4000),
    ("e3", 2024, "2024-03-09", "club_b", "club_c", 0, 0, 4500),
    ("e4", 2024, "2024-03-09", "club_d", "club_a", 1, 3, 3000),
    ("e5", 2024, "2024-03-16", "club_a", "club_c", 1, 1, 5500),
    ("e6", 2024, "2024-03-16", "club_b", "club_d", 2, 1, 4200),
    ("e7", 2024, "2024-03-23", "club_b", "club_a", 0, 1, 4100),
    ("e8", 2024, "2024-03-23", "club_d", "club_c", 0, 0, 2900),
    ("e9", 2024, "2024-03-30", "club_b", "club_c", 1, 0, 3800),
    ("e10", 2024, "2024-03-30", "club_d", "club_a", 0, 2, 2600),
]
ELIMINATION_CLUBS = [(f"club_{c}", 2024, "East", f"Club {c.upper()}") for c in "abcd"]
ELIMINATION_STRUCTURE = pd.DataFrame(
    [(2024, "East", 1, 1, "one playoff spot, one relegation spot")], columns=STRUCTURE_COLUMNS
)


# ---------------------------------------------------------------------------
# The definitions contract
# ---------------------------------------------------------------------------


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


def test_mart_columns_match_definitions(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """Every defined feature exists as a mart column, and vice versa, in order.

    Both directions. A feature defined but not built fails training with a
    KeyError; a column built but not defined is dead weight nobody removes. The
    types are part of the contract too: booleans as BOOLEAN, counts as INTEGER,
    moving averages as DOUBLE.
    """
    build_mart(con, tiny_season, tiny_clubs)
    described = con.execute("DESCRIBE mart_match_features").fetchall()
    assert [r[0] for r in described] == list(mart_columns())
    types = {r[0]: r[1] for r in described}
    assert types["is_derby"] == "BOOLEAN"
    assert types["is_mathematically_live"] == "BOOLEAN"
    assert types["matches_since_elimination"] == "INTEGER"
    assert types["rank_gap"] == "INTEGER"
    assert types["home_gate_ma3"] == "DOUBLE"
    assert types["home_gate_ma5"] == "DOUBLE"
    assert types["day_of_week"] == "INTEGER"
    assert types["opponent_club_id"] == "VARCHAR"


# ---------------------------------------------------------------------------
# Calendar and lag
# ---------------------------------------------------------------------------


def test_lag_window_excludes_the_current_match(con: duckdb.DuckDBPyConnection) -> None:
    """home_gate_ma3 must not include the match it is a feature of.

    club_a's first three gates are 1000 and the fourth is 99999. On the fourth
    match every lag is still 1000: last_home_gate, ma3, ma5. A window ending at
    CURRENT ROW - SQL's default frame - would put 99999 into its own features.
    On the fifth match the 99999 has moved into history: ma3 is (1000 + 1000 +
    99999) / 3 and ma5 averages the four gates that exist.
    """
    build_mart(con, matches(LAG_MATCHES), clubs(LAG_CLUBS))
    rows = mart(con)
    assert (rows["l1"]["last_home_gate"], rows["l1"]["home_gate_ma3"]) == (None, None)
    assert rows["l1"]["home_gate_ma5"] is None
    assert rows["l2"]["last_home_gate"] == 1000
    assert rows["l4"]["last_home_gate"] == 1000
    assert rows["l4"]["home_gate_ma3"] == pytest.approx(1000.0)
    assert rows["l4"]["home_gate_ma5"] == pytest.approx(1000.0)
    assert rows["l5"]["last_home_gate"] == 99999
    assert rows["l5"]["home_gate_ma3"] == pytest.approx((1000 + 1000 + 99999) / 3)
    assert rows["l5"]["home_gate_ma5"] == pytest.approx((3 * 1000 + 99999) / 4)
    # exactly five rows in the window: l1..l5 for l6, l2..l6 for l7
    assert rows["l6"]["home_gate_ma5"] == pytest.approx((4 * 1000 + 99999) / 5)
    assert rows["l7"]["home_gate_ma5"] == pytest.approx((3 * 1000 + 99999 + 2000) / 5)
    assert rows["l7"]["home_gate_ma3"] == pytest.approx((99999 + 1000 + 2000) / 3)


def test_first_home_match_lag_is_null_and_that_is_allowed(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """Some nulls are correct.

    A club's first ever home match has no last_home_gate. config.ALLOWED_NULL_FEATURES
    is where that decision is encoded, and features_not_null must respect it:
    four clubs, four first home matches, four permitted nulls, and a pass.
    """
    build_mart(con, tiny_season, tiny_clubs)
    first_home = mart(con)["m1"]
    assert first_home["last_home_gate"] is None
    assert first_home["home_gate_ma3"] is None
    assert first_home["home_gate_ma5"] is None
    result = features_not_null(con)
    assert result.passed, result.metadata
    assert result.metadata["null_counts"] == {}
    assert result.metadata["allowed_with_nulls"]["last_home_gate"] == 4
    assert set(result.metadata["allowed_with_nulls"]) <= config.ALLOWED_NULL_FEATURES


def test_features_not_null_fails_on_a_null_outside_the_allowed_set(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """Demo scenario D4: a null in a pro-rel feature stops the run naming the column."""
    build_mart(con, tiny_season, tiny_clubs)
    con.execute("UPDATE mart_match_features SET rank_before = NULL WHERE match_id = 'm3'")
    result = features_not_null(con)
    assert not result.passed
    assert result.metadata["null_counts"] == {"rank_before": 1}
    assert result.tier == "mart"


def test_mart_matches_staging_fires_on_a_dropped_or_duplicated_row(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """Fewer mart rows means a join dropped matches; more means one fanned out.

    Unplayed fixtures are in the mart by design, so the mart carries every
    staging match, one row each, and the count is the whole contract.
    """
    build_mart(con, tiny_season, tiny_clubs)
    assert mart_matches_staging(con).passed
    con.execute("DELETE FROM mart_match_features WHERE match_id = 'm3'")
    dropped = mart_matches_staging(con)
    assert not dropped.passed
    assert dropped.tier == "mart"
    assert dropped.metadata == {"mart_rows": 5, "staging_rows": 6, "difference": -1, "void_rows": 0}
    runner.materialise(con, "mart_match_features")
    con.execute(
        "INSERT INTO mart_match_features SELECT * FROM mart_match_features WHERE match_id = 'm3'"
    )
    fanned = mart_matches_staging(con)
    assert not fanned.passed
    assert fanned.metadata == {"mart_rows": 7, "staging_rows": 6, "difference": 1, "void_rows": 0}


def test_lags_cross_seasons_and_same_fixture_last_season(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A club's first home match of a season takes its lags from the previous one.

    club_a opens 2024 with last_home_gate 5100 (its last 2023 home gate) and
    ma3 = (4800 + 5100) / 2. same_fixture_last_season looks at the same
    (home, away) pairing in season - 1: 5100 for s4 (the more recent of two
    meetings), 3900 for s5, and NULL for every 2023 row.
    """
    build_mart(con, matches(TWO_SEASON_MATCHES), clubs(TWO_SEASON_CLUBS))
    rows = mart(con)
    assert rows["s4"]["last_home_gate"] == 5100
    assert rows["s4"]["home_gate_ma3"] == pytest.approx((4800 + 5100) / 2)
    assert rows["s4"]["is_season_opener"] is True
    assert rows["s4"]["same_fixture_last_season"] == 5100
    assert rows["s5"]["same_fixture_last_season"] == 3900
    assert rows["s5"]["last_home_gate"] == 3900
    assert all(rows[m]["same_fixture_last_season"] is None for m in ("s1", "s2", "s3"))
    assert rows["s3"]["is_final_home_match"] is True
    assert mart_matches_staging(con).passed


def test_covid_flag_covers_the_configured_range(
    con: duckdb.DuckDBPyConnection,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_covid_affected matches config.COVID_START and COVID_END, and lags skip it.

    The range is a judgement call, so the test pins the flag to the config
    rather than to specific dates: the window is moved onto the fixture's
    second round, the flag is compared row by row against the config, and the
    lag history is shown to step over the flagged matches - club_b's only
    earlier home match is inside the window, so its next home match has no
    last_home_gate at all rather than an empty-stadium figure.
    """
    monkeypatch.setattr(config, "COVID_START", dt.date(2024, 3, 5))
    monkeypatch.setattr(config, "COVID_END", dt.date(2024, 3, 10))
    stage_from_raw(con, tiny_raw, club_aliases, tiny_clubs)
    flags = con.execute("SELECT match_id, date, is_covid_affected FROM stg_matches").fetchall()
    assert len(flags) == 6
    for match_id, date, flag in flags:
        assert flag is (config.COVID_START <= date <= config.COVID_END), match_id
    assert {m for m, _, f in flags if f} == {"m3", "m4"}

    for model in DOWNSTREAM_MODELS:
        runner.materialise(con, model)
    rows = mart(con)
    assert rows["m3"]["is_covid_affected"] is True
    assert rows["m6"]["last_home_gate"] is None  # m3 was club_b's only earlier home match
    assert rows["m5"]["last_home_gate"] == 5000  # m1 is outside the window
    assert rows["m3"]["rank_before"] == 4  # a COVID match still gets its context features
    assert features_not_null(con).passed


# ---------------------------------------------------------------------------
# Match context
# ---------------------------------------------------------------------------


def test_season_opener_and_final_home_match(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """First and last home fixture of the season per club, by date.

    club_a hosts m1 and m5; club_c hosts only m2, which is therefore both.
    """
    build_mart(con, tiny_season, tiny_clubs)
    rows = mart(con)
    flags = {m: (r["is_season_opener"], r["is_final_home_match"]) for m, r in rows.items()}
    assert flags == {
        "m1": (True, False),
        "m2": (True, True),
        "m3": (True, False),
        "m4": (True, True),
        "m5": (False, True),
        "m6": (False, True),
    }


def test_is_derby_in_either_direction(con: duckdb.DuckDBPyConnection) -> None:
    """derbies.csv lists a pair once; both home/away orderings are derbies."""
    derbies = pd.DataFrame(
        [("club_a", "club_b", "test")], columns=["club_id_a", "club_id_b", "note"]
    )
    build_mart(con, matches(ELIMINATION_MATCHES), clubs(ELIMINATION_CLUBS), derbies=derbies)
    rows = mart(con)
    assert rows["e1"]["is_derby"] is True  # club_a v club_b
    assert rows["e7"]["is_derby"] is True  # club_b v club_a
    assert rows["e2"]["is_derby"] is False
    assert sum(r["is_derby"] for r in rows.values()) == 2  # type: ignore[misc]
    assert mart_matches_staging(con).passed  # the derby join did not fan out


def test_unplayed_fixtures_get_features_and_null_attendance(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """Forecasts for remaining home matches need their features.

    With the third round unplayed, m5 (club_a v club_c) is still in the mart:
    attendance NULL, is_played false, and every context and pro-rel feature
    populated from the table as it stands after two rounds - club_a top on 6
    points, one match left, four points clear of the playoff line (position 2,
    club_c on 2), and its last home gate the 5000 from m1.
    """
    build_mart(con, with_unplayed(tiny_season, ["m5", "m6"]), tiny_clubs)
    rows = mart(con)
    assert len(rows) == 6
    m5 = rows["m5"]
    assert m5["is_played"] is False
    assert m5["attendance"] is None
    assert m5["rank_before"] == 1
    assert m5["opponent_rank_before"] == 2
    assert m5["rank_gap"] == 1
    assert m5["matches_remaining"] == 1
    assert m5["points_from_playoff_line"] == -4
    assert m5["is_mathematically_live"] is True
    assert m5["matches_since_elimination"] == -1
    assert m5["last_home_gate"] == 5000
    assert m5["is_final_home_match"] is True
    assert rows["m6"]["rank_before"] == 4
    assert rows["m6"]["last_home_gate"] == 4500
    assert features_not_null(con).passed
    assert mart_matches_staging(con).passed


def test_opponent_rank_is_in_its_own_conference(con: duckdb.DuckDBPyConnection) -> None:
    """opponent_rank_before comes from the away club's conference, not the home club's.

    y5 is club_a (East, top on +3) at home to club_e (West, top on +1). Both
    are ranked 1 in their own conference, so rank_gap is 0. A league-wide rank
    would put club_e second.
    """
    build_mart(con, matches(TWO_CONFERENCE_MATCHES), clubs(TWO_CONFERENCE_CLUBS))
    y5 = mart(con)["y5"]
    assert (y5["rank_before"], y5["opponent_rank_before"], y5["rank_gap"]) == (1, 1, 0)
    y6 = mart(con)["y6"]  # club_b (East, 4th) v club_f (West, 4th)
    assert (y6["rank_before"], y6["opponent_rank_before"], y6["rank_gap"]) == (4, 4, 0)


# ---------------------------------------------------------------------------
# Pro-rel: the elimination fixture
# ---------------------------------------------------------------------------


def test_is_mathematically_live_and_matches_since_elimination(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The stakes arithmetic on the elimination fixture, per the table in its comment.

    club_d goes out on 03-23 (1 + 3 * 2 = 7, not strictly more than the leader's
    7); club_b and club_c go out on 03-30 (4 + 3 = 7 against 10). So club_d's
    home matches read 0 then 1 matches since elimination, club_b's last home
    match reads 0, and everything else is -1.
    """
    build_mart(
        con, matches(ELIMINATION_MATCHES), clubs(ELIMINATION_CLUBS), structure=ELIMINATION_STRUCTURE
    )
    rows = mart(con)
    live = {m: r["is_mathematically_live"] for m, r in rows.items()}
    assert live == {
        "e1": True,
        "e2": True,
        "e3": True,
        "e4": True,
        "e5": True,
        "e6": True,
        "e7": True,
        "e8": False,
        "e9": False,
        "e10": False,
    }
    since = {m: r["matches_since_elimination"] for m, r in rows.items()}
    assert since == {
        "e1": -1,
        "e2": -1,
        "e3": -1,
        "e4": -1,
        "e5": -1,
        "e6": -1,
        "e7": -1,
        "e8": 0,
        "e9": 0,
        "e10": 1,
    }
    # points from the line: the line club's points minus the home club's
    assert rows["e1"]["points_from_playoff_line"] == 0
    assert rows["e3"]["points_from_playoff_line"] == 3  # line 3 (club_a), club_b on 0
    assert rows["e8"]["points_from_playoff_line"] == 6  # line 7, club_d on 1
    assert rows["e10"]["points_from_playoff_line"] == 8  # line 10, club_d on 2
    assert rows["e5"]["points_from_playoff_line"] == 0  # club_a IS the line club
    assert rows["e1"]["matches_remaining"] == 5
    assert rows["e8"]["matches_remaining"] == 2
    assert rows["e10"]["matches_remaining"] == 1
    # the relegation line is the last SAFE place (position 3 of 4)
    assert rows["e10"]["points_from_relegation_line"] == 2  # 3rd (club_b, 4) minus club_d's 2
    assert rows["e9"]["points_from_relegation_line"] == 0  # club_b IS third
    assert rows["e7"]["points_from_relegation_line"] == -1  # 3rd (club_c, 3) minus club_b's 4
    assert features_not_null(con).passed

    eliminated = dict(
        con.execute(
            "SELECT date, eliminated_on FROM int_stakes WHERE club_id = 'club_d' ORDER BY date"
        ).fetchall()
    )
    assert eliminated[dt.date(2024, 3, 16)] is None
    assert eliminated[dt.date(2024, 3, 23)] == dt.date(2024, 3, 23)
    assert eliminated[dt.date(2024, 3, 30)] == dt.date(2024, 3, 23)


def test_decay_curve_rows_carry_n(con: duckdb.DuckDBPyConnection) -> None:
    """Exercise 6.3 on the elimination fixture, indexed against each club's own baseline.

    club_d's baseline is its one live home gate, 3000; its eliminated home
    matches drew 2900 (0 since) and 2600 (1 since). club_b's baseline is the
    mean of 4500, 4200, 4100; its eliminated home match drew 3800 (0 since).
    So the curve has two points: 0 since with n = 2 from two club-seasons, and
    1 since with n = 1.
    """
    build_mart(
        con, matches(ELIMINATION_MATCHES), clubs(ELIMINATION_CLUBS), structure=ELIMINATION_STRUCTURE
    )
    curve = con.execute("SELECT * FROM mart_decay_curve ORDER BY 1").fetchall()
    assert [r[0] for r in curve] == [0, 1]
    zero, one = curve
    assert (zero[1], zero[4]) == (2, 2)
    assert zero[2] == pytest.approx((2900 / 3000 + 3800 / (12800 / 3)) / 2)
    assert zero[3] == pytest.approx(3350.0)
    assert (one[1], one[4]) == (1, 1)
    assert one[2] == pytest.approx(2600 / 3000)
    assert one[3] == pytest.approx(2600.0)


def test_missing_structure_row_gives_null_stakes_not_an_invented_number(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A season nobody has looked up yet must stop the run, not get a default.

    With no conference_structure row for (2024, East) and no
    config.DEFAULT_PLAYOFF_SPOTS, the playoff-line features are NULL and
    features_not_null names them. The relegation line still has its assumed
    fallback, and matches_since_elimination stays -1 rather than going null.
    """
    monkeypatch.setattr(config, "DEFAULT_PLAYOFF_SPOTS", None)
    wrong_conference = pd.DataFrame([(2024, "West", 2, 1, "not East")], columns=STRUCTURE_COLUMNS)
    build_mart(con, tiny_season, tiny_clubs, structure=wrong_conference)
    result = features_not_null(con)
    assert not result.passed
    assert result.metadata["null_counts"] == {
        "points_from_playoff_line": 6,
        "is_mathematically_live": 6,
    }
    rows = mart(con)
    assert {r["matches_since_elimination"] for r in rows.values()} == {-1}
    assert all(r["points_from_relegation_line"] is not None for r in rows.values())


# ---------------------------------------------------------------------------
# Void fixtures and unplayed fixtures after elimination
# ---------------------------------------------------------------------------


def test_void_fixture_is_kept_in_staging_and_counts_for_nothing(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A cancelled fixture stays in stg_matches flagged is_void and is otherwise absent.

    It creates no standings date, adds nothing to matches_remaining, has no
    mart row and so gets no forecast, and does not make an earlier match stop
    being the club's final home match. mart_matches_staging expects exactly
    that and reports the void count.
    """
    cancelled = pd.concat(
        [
            tiny_season,
            pd.DataFrame(
                [("m7", 2024, "2024-03-23", "club_a", "club_b", None, None, None)],
                columns=tiny_season.columns,
            ),
        ],
        ignore_index=True,
    )
    stage_frames(con, with_unplayed(cancelled, ["m7"]), tiny_clubs, void=["m7"])
    for model in DOWNSTREAM_MODELS:
        runner.materialise(con, model)

    assert con.execute("SELECT is_void FROM stg_matches WHERE match_id = 'm7'").fetchone() == (
        True,
    )
    assert con.execute("SELECT count(*) FROM stg_matches").fetchone() == (7,)
    assert con.execute("SELECT max(date) FROM int_standings").fetchone() == (dt.date(2024, 3, 17),)
    assert con.execute(
        "SELECT max(fixtures_total) FROM int_stakes WHERE club_id = 'club_a'"
    ).fetchone() == (3,)
    rows = mart(con)
    assert "m7" not in rows and len(rows) == 6
    assert rows["m5"]["is_final_home_match"] is True
    result = mart_matches_staging(con)
    assert result.passed
    assert (result.metadata["staging_rows"], result.metadata["void_rows"]) == (6, 1)


def test_matches_since_elimination_counts_unplayed_home_fixtures(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """An unplayed home fixture after elimination is a match since elimination too.

    Unplayed fixtures are in the mart so their features can be forecast, and
    for club_d's home fixture on 04-06 (e11, unplayed) that feature is 0; the
    played home match a week later (e12) reads 1 because e11 counts. On 03-30
    club_d is still live: 2 points plus three matches left reaches 11 against
    club_a's 10.
    """
    extended = ELIMINATION_MATCHES + [
        ("e11", 2024, "2024-04-06", "club_d", "club_c", None, None, None),
        ("e12", 2024, "2024-04-13", "club_d", "club_b", 0, 1, 2500),
    ]
    build_mart(
        con,
        with_unplayed(matches(extended), ["e11"]),
        clubs(ELIMINATION_CLUBS),
        structure=ELIMINATION_STRUCTURE,
    )
    rows = mart(con)
    assert rows["e10"]["matches_since_elimination"] == -1
    assert rows["e11"]["matches_since_elimination"] == 0
    assert rows["e12"]["matches_since_elimination"] == 1
    assert rows["e11"]["is_played"] is False
