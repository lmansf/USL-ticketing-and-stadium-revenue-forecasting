"""Standings reconstruction: point-in-time correctness and tie-breaking.

The most important test file here. Point-in-time leakage does not raise - it
shows up as suspiciously good validation error, which is easy to mistake for
success.

Two kinds of test. The hand-checkable ones run on tiny_season and its
extensions, where the right answer was worked out on paper. The real-data one
runs the whole layer on the committed example season (EPL 2018/19) and pins
the final table to an independent pure-Python recomputation first, and to the
published record second - a check written in the same language as the thing
it checks inherits its bugs, so the Python recompute is the arbiter.

Doc: docs/phases/04-standings-as-of-match-date.md
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import duckdb
import pandas as pd
from conftest import stage_frames

from usl import config
from usl.features.definitions import all_features
from usl.load.raw import RAW_COLUMNS, ensure_raw_tables
from usl.logging_setup import ensure_log_tables, new_run_context
from usl.transform import reference, runner
from usl.transform.checks import (
    INTERMEDIATE_CHECKS,
    MART_CHECKS,
    STAGING_CHECKS,
    all_club_seasons_have_conference,
    no_future_leakage,
    one_match_per_club_per_date,
)

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

# The published 2018/19 Premier League table: (club_id, points, goal difference),
# in finishing order. Every row was verified against the pure-Python recompute
# from the archived payload's goal counts before it was pinned here.
PUBLISHED_2018_19: list[tuple[str, int, int]] = [
    ("manchester_city", 98, 72),
    ("liverpool", 97, 67),
    ("chelsea", 72, 24),
    ("tottenham_hotspur", 71, 28),
    ("arsenal", 70, 22),
    ("manchester_united", 66, 11),
    ("wolverhampton_wanderers", 57, 1),
    ("everton", 54, 8),
    ("leicester_city", 52, 3),
    ("west_ham_united", 52, -3),
    ("watford", 50, -7),
    ("crystal_palace", 49, -2),
    ("newcastle_united", 45, -6),
    ("bournemouth", 45, -14),
    ("burnley", 40, -23),
    ("southampton", 39, -20),
    ("brighton", 36, -25),
    ("cardiff_city", 34, -35),
    ("fulham", 26, -47),
    ("huddersfield_town", 16, -54),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def matches(rows: list[tuple[object, ...]]) -> pd.DataFrame:
    """tiny_season-shaped rows."""
    return pd.DataFrame(rows, columns=MATCH_COLUMNS)


def clubs(rows: list[tuple[object, ...]]) -> pd.DataFrame:
    """tiny_clubs-shaped rows."""
    return pd.DataFrame(rows, columns=CLUB_COLUMNS)


def build_standings(
    con: duckdb.DuckDBPyConnection, season: pd.DataFrame, club_rows: pd.DataFrame
) -> None:
    """Stage the frames and materialise int_standings from the real SQL file."""
    stage_frames(con, season, club_rows)
    runner.materialise(con, "int_standings")


def materialise_mutated_standings(con: duckdb.DuckDBPyConnection, old: str, new: str) -> None:
    """Rebuild int_standings from the real SQL file with one fragment replaced.

    A mutation of the file that is actually run, rather than a hand-written
    leaky table, so the test proves the check catches a mistake in THIS SQL.
    Fails loudly if the fragment is no longer there, so a refactor of the file
    cannot turn the mutation into a no-op that passes for the wrong reason.
    """
    sql = (config.SQL_DIR / "int_standings.sql").read_text(encoding="utf-8")
    assert sql.count(old) == 1, f"expected exactly one occurrence of {old!r} in int_standings.sql"
    con.execute("CREATE OR REPLACE TABLE int_standings AS " + sql.replace(old, new))


def with_unplayed(frame: pd.DataFrame, match_ids: list[str]) -> pd.DataFrame:
    """Blank the result and gate of the named fixtures, as unplayed rows."""
    out = frame.copy()
    unplayed = out["match_id"].isin(match_ids)
    for col in ("home_goals", "away_goals", "attendance"):
        out[col] = pd.array(out[col].tolist(), dtype="Int64")
        out.loc[unplayed, col] = pd.NA
    return out


def standings_on(con: duckdb.DuckDBPyConnection, date: str) -> dict[str, dict[str, object]]:
    """int_standings rows for one date, keyed by club_id."""
    rows = con.execute(
        """
        SELECT club_id, conference, is_match_date, played_before, pts_before,
               gd_before, gf_before, rank_before, n_clubs
        FROM int_standings WHERE date = CAST(? AS DATE)
        """,
        [date],
    ).fetchall()
    keys = [
        "conference",
        "is_match_date",
        "played_before",
        "pts_before",
        "gd_before",
        "gf_before",
        "rank_before",
        "n_clubs",
    ]
    return {r[0]: dict(zip(keys, r[1:], strict=True)) for r in rows}


def final_table(
    con: duckdb.DuckDBPyConnection, season: int, conference: str
) -> list[tuple[str, int, int, int, int, int]]:
    """The snapshot rows of a conference-season, in rank order.

    The snapshot is the row the day after the conference's last fixture, where
    every club's totals include every played match and is_match_date is false.

    Returns:
        (club_id, played, pts, gd, gf, rank) tuples.
    """
    rows = con.execute(
        """
        SELECT club_id, played_before, pts_before, gd_before, gf_before, rank_before
        FROM int_standings
        WHERE season = ? AND conference = ?
          AND date = (SELECT max(date) FROM int_standings WHERE season = ? AND conference = ?)
          AND NOT is_match_date
        ORDER BY rank_before, club_id
        """,
        [season, conference, season, conference],
    ).fetchall()
    return [tuple(r) for r in rows]  # type: ignore[misc]


def load_example_raw(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Build raw_matches straight from the archived league-matches payload.

    Lifts the same fourteen fields the loader lifts, every value as text, so
    this test does not depend on the loader being finished. Returns the row
    count.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = [
        {
            "match_id": f"fs:{d['id']}",
            "provider_id": str(d["id"]),
            "season_id": config.EXAMPLE_SEASON_ID,
            "season_raw": str(d["season"]),
            "date_unix": int(d["date_unix"]),
            "status": str(d["status"]),
            "game_week": str(d["game_week"]),
            "home_raw": str(d["homeID"]),
            "away_raw": str(d["awayID"]),
            "home_name": str(d["home_name"]),
            "away_name": str(d["away_name"]),
            "home_goals": str(d["homeGoalCount"]),
            "away_goals": str(d["awayGoalCount"]),
            "attendance": str(d["attendance"]),
            "stadium_name": str(d["stadium_name"]),
            "raw_json": json.dumps(d),
            "ingested_at": dt.datetime(2026, 9, 1, 6, 0, 0),
            "source_endpoint": "league-matches",
        }
        for d in payload["data"]
    ]
    frame = pd.DataFrame(rows)
    ensure_raw_tables(con)
    con.register("example_raw", frame)
    con.execute(f"INSERT INTO raw_matches SELECT {', '.join(RAW_COLUMNS)} FROM example_raw")
    con.unregister("example_raw")
    return len(rows)


def python_final_table(path: Path) -> dict[str, tuple[int, int, int]]:
    """The final table recomputed in pure Python from the payload's goal counts.

    Independent of every line of SQL in the project. Provider ids are mapped to
    club_ids through club_aliases.csv read with the csv module, so the only
    thing shared with the pipeline is the mapping file itself.

    Returns:
        club_id -> (points, goal difference, goals for).
    """
    aliases: dict[str, str] = {}
    with open(config.CLUB_ALIASES_CSV, encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            aliases[reference.normalize_club_key(rec["raw_name"])] = rec["club_id"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    table: dict[str, list[int]] = {}
    for d in payload["data"]:
        if d["status"] != "complete":
            continue
        home, away = aliases[str(d["homeID"])], aliases[str(d["awayID"])]
        hg, ag = int(d["homeGoalCount"]), int(d["awayGoalCount"])
        for club, gf, ga in ((home, hg, ag), (away, ag, hg)):
            totals = table.setdefault(club, [0, 0, 0])
            totals[0] += 3 if gf > ga else 1 if gf == ga else 0
            totals[1] += gf - ga
            totals[2] += gf
    return {club: (t[0], t[1], t[2]) for club, t in table.items()}


def assert_reference_csvs_parse(con: duckdb.DuckDBPyConnection) -> None:
    """Every reference CSV must load under exactly the columns its header declares.

    A CSV whose rows carry unquoted commas makes DuckDB's dialect sniffer fall
    back to a single column, and every join against that table then fails to
    bind. This names the file and the fix rather than leaving a binder error.
    """
    for name, path in reference.REFERENCE_CSVS.items():
        declared = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        reference.read_reference_csv(con, name, path)
        found = [r[0] for r in con.execute(f'DESCRIBE "{name}"').fetchall()]
        assert found == declared, (
            f"{path.name} loaded with columns {found} instead of its header {declared}. "
            "A value containing a comma must be quoted (RFC 4180); check the note column."
        )


# ---------------------------------------------------------------------------
# Hand-checkable fixtures
# ---------------------------------------------------------------------------


def test_first_match_of_season_has_zero_points(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """pts_before, gd_before, and played_before are all zero, not null.

    The ASOF join finds nothing before the first match date, and a null there
    would propagate into rank_before and every feature built on it. COALESCE
    turns it into a genuine zero, and with every club level RANK() gives them
    all position 1.
    """
    build_standings(con, tiny_season, tiny_clubs)
    opening = standings_on(con, "2024-03-02")
    assert set(opening) == {"club_a", "club_b", "club_c", "club_d"}
    for club, row in opening.items():
        assert row["played_before"] == 0, club
        assert row["pts_before"] == 0, club
        assert row["gd_before"] == 0, club
        assert row["gf_before"] == 0, club
        assert row["rank_before"] == 1, club
        assert row["is_match_date"] is True, club
        assert row["n_clubs"] == 4


def test_points_before_second_match_equals_first_match_result(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """Club A won its first match 2-0, so on its second match it has 3 points.

    This is the leakage test in its smallest form. If pts_before on match two
    already includes match two's result (club_a wins 3-1 away at club_d), the
    number is 6 rather than 3 - wrong in an obvious direction, and obvious only
    because this fixture is small enough to check by hand.
    """
    build_standings(con, tiny_season, tiny_clubs)
    club_a = standings_on(con, "2024-03-09")["club_a"]
    assert club_a["played_before"] == 1
    assert club_a["pts_before"] == 3
    assert club_a["gd_before"] == 2
    assert club_a["gf_before"] == 2
    assert club_a["rank_before"] == 1
    # club_b lost that match: 0 points, -2 goal difference, bottom of the four
    club_b = standings_on(con, "2024-03-09")["club_b"]
    assert (club_b["pts_before"], club_b["gd_before"], club_b["rank_before"]) == (0, -2, 4)


def test_no_row_uses_a_result_on_or_after_its_own_date(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """The general form of the leakage test, and proof that the check can fire.

    checks.no_future_leakage recomputes played, points, goal difference and
    goals for independently from matches strictly before each row's date. It
    passes on the real SQL; it must FAIL on each of the classic mistakes:

    1. the running total with a frame ending at CURRENT ROW, hand-written
       below, which folds each match's own result into the row that is
       supposed to predict it;
    2. the real int_standings.sql with its ASOF join made non-strict
       (grid.date >= running.date, the form the phase 04 sketch uses), which
       with running totals that include the current match leaks on every
       match date - twelve of the sixteen rows, every one except the four on
       the opening day where there is nothing yet to leak;
    3. the real file with goals for and against swapped on the home side,
       which leaves points and played intact and is visible only in the goal
       columns - the tie-breakers the check would miss if it compared points
       alone.
    """
    build_standings(con, tiny_season, tiny_clubs)
    good = no_future_leakage(con)
    assert good.passed, good.metadata
    assert good.tier == "intermediate"
    assert good.metadata["rows_checked"] == 16  # 4 clubs x (3 match dates + 1 snapshot)
    assert good.metadata["mismatches"] == []

    # 2. the non-strict ASOF join on the real file
    materialise_mutated_standings(con, "AND g.date > r.date", "AND g.date >= r.date")
    non_strict = no_future_leakage(con)
    assert not non_strict.passed
    assert non_strict.metadata["rows_checked"] == 16
    assert non_strict.metadata["n_mismatches"] == 12
    assert {m["date"] for m in non_strict.metadata["mismatches"]} >= {"2024-03-02", "2024-03-09"}
    opener = next(m for m in non_strict.metadata["mismatches"] if m["club_id"] == "club_a")
    assert (opener["date"], opener["pts_before"], opener["pts_expected"]) == ("2024-03-02", 3, 0)

    # 3. gf and ga swapped on the real file: points and played still agree
    materialise_mutated_standings(
        con,
        "        home_goals   AS gf,\n        away_goals   AS ga\n",
        "        away_goals   AS gf,\n        home_goals   AS ga\n",
    )
    swapped = no_future_leakage(con)
    assert not swapped.passed
    first_swapped = swapped.metadata["mismatches"][0]
    assert first_swapped["pts_before"] == first_swapped["pts_expected"]
    assert first_swapped["played_before"] == first_swapped["played_expected"]
    assert first_swapped["gf_before"] != first_swapped["gf_expected"]

    # 1. the hand-written leaky version: the same shape, computed INCLUDING the current match.
    con.execute(
        """
        CREATE OR REPLACE TABLE int_standings AS
        WITH club_matches AS (
            SELECT season, date, home_club_id AS club_id,
                   CASE WHEN home_goals > away_goals THEN 3
                        WHEN home_goals = away_goals THEN 1 ELSE 0 END AS points,
                   home_goals AS gf, away_goals AS ga
            FROM stg_matches WHERE is_played
            UNION ALL
            SELECT season, date, away_club_id,
                   CASE WHEN away_goals > home_goals THEN 3
                        WHEN away_goals = home_goals THEN 1 ELSE 0 END,
                   away_goals, home_goals
            FROM stg_matches WHERE is_played
        ),
        running AS (
            SELECT season, 'East' AS conference, club_id, date, TRUE AS is_match_date,
                   CAST(COUNT(*)     OVER w AS INTEGER) AS played_before,
                   CAST(SUM(points)  OVER w AS INTEGER) AS pts_before,
                   CAST(SUM(gf - ga) OVER w AS INTEGER) AS gd_before,
                   CAST(SUM(gf)      OVER w AS INTEGER) AS gf_before
            FROM club_matches
            WINDOW w AS (
                PARTITION BY season, club_id ORDER BY date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            )
        )
        SELECT *,
               CAST(RANK() OVER (
                   PARTITION BY season, conference, date
                   ORDER BY pts_before DESC, gd_before DESC, gf_before DESC
               ) AS INTEGER) AS rank_before,
               4 AS n_clubs
        FROM running
        """
    )
    leaky = no_future_leakage(con)
    assert not leaky.passed
    assert leaky.metadata["rows_checked"] == 12
    # every row folds its own match in, so every row's played count is one too high
    assert leaky.metadata["n_mismatches"] == 12
    first = leaky.metadata["mismatches"][0]
    assert first["played_before"] == first["played_expected"] + 1
    assert isinstance(first["date"], str)  # JSON-serialisable for check_log
    assert set(first) == {
        "season",
        "conference",
        "club_id",
        "date",
        "played_before",
        "played_expected",
        "pts_before",
        "pts_expected",
        "gd_before",
        "gd_expected",
        "gf_before",
        "gf_expected",
    }


def test_final_standings_match_hand_computed_table(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """Final table from tiny_season, worked out on paper.

    After six matches: club_a has 7 points (W, W, D), club_b has 4 (L, D, W),
    club_c has 3 (D, D, D), club_d has 1 (D, L, L). Goal differences are +4, -1,
    0, and -3; goals for 6, 2, 2, 3. The snapshot row the day after the last
    fixture is where the completed table lives, and it is not a match date.
    """
    build_standings(con, tiny_season, tiny_clubs)
    assert final_table(con, 2024, "East") == [
        ("club_a", 3, 7, 4, 6, 1),
        ("club_b", 3, 4, -1, 2, 2),
        ("club_c", 3, 3, 0, 2, 3),
        ("club_d", 3, 1, -3, 3, 4),
    ]
    snapshot_date = con.execute("SELECT max(date) FROM int_standings").fetchone()
    assert snapshot_date == (dt.date(2024, 3, 17),)


def test_ties_share_a_position(con: duckdb.DuckDBPyConnection) -> None:
    """RANK(), not ROW_NUMBER().

    After one round club_a and club_c have both won 1-0: level on points, goal
    difference, and goals for. They must share position 1, and the two losers
    share position 3 - there is no position 2. ROW_NUMBER() would split them
    by whatever order the engine felt like, and rank would jitter between runs
    on identical data.
    """
    season = matches(
        [
            ("t1", 2024, "2024-03-02", "club_a", "club_b", 1, 0, 5000),
            ("t2", 2024, "2024-03-02", "club_c", "club_d", 1, 0, 4000),
            ("t3", 2024, "2024-03-09", "club_a", "club_c", 2, 2, 5000),
            ("t4", 2024, "2024-03-09", "club_b", "club_d", 0, 0, 4000),
        ]
    )
    club_rows = clubs(
        [
            ("club_a", 2024, "East", "Club A"),
            ("club_b", 2024, "East", "Club B"),
            ("club_c", 2024, "East", "Club C"),
            ("club_d", 2024, "East", "Club D"),
        ]
    )
    build_standings(con, season, club_rows)
    second_round = standings_on(con, "2024-03-09")
    ranks = {club: row["rank_before"] for club, row in second_round.items()}
    assert ranks == {"club_a": 1, "club_c": 1, "club_b": 3, "club_d": 3}


# Two conferences with an interconference fixture in the second round. After
# round one club_a leads the East on +3 and club_e leads the West on +1;
# league-wide club_e would be second, within conference each is first.
TWO_CONFERENCE_MATCHES = [
    ("x1", 2024, "2024-03-02", "club_a", "club_b", 3, 0, 5000),
    ("x2", 2024, "2024-03-02", "club_c", "club_d", 0, 0, 4000),
    ("x3", 2024, "2024-03-02", "club_e", "club_f", 1, 0, 6000),
    ("x4", 2024, "2024-03-02", "club_g", "club_h", 0, 0, 3500),
    ("x5", 2024, "2024-03-09", "club_a", "club_e", 1, 1, 5200),
    ("x6", 2024, "2024-03-09", "club_b", "club_f", 2, 0, 4100),
    ("x7", 2024, "2024-03-09", "club_c", "club_g", 0, 1, 3900),
    ("x8", 2024, "2024-03-09", "club_d", "club_h", 1, 1, 3000),
]
TWO_CONFERENCE_CLUBS = [
    ("club_a", 2024, "East", "Club A"),
    ("club_b", 2024, "East", "Club B"),
    ("club_c", 2024, "East", "Club C"),
    ("club_d", 2024, "East", "Club D"),
    ("club_e", 2024, "West", "Club E"),
    ("club_f", 2024, "West", "Club F"),
    ("club_g", 2024, "West", "Club G"),
    ("club_h", 2024, "West", "Club H"),
]


def test_rank_is_within_conference_not_league_wide(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Two conferences, and each has its own club ranked 1.

    This project ranks within conference. On the second match date club_a (East,
    +3) and club_e (West, +1) are each ranked 1 in their own conference, with
    n_clubs = 4 on both sides. League-wide club_e would be ranked 2, so this
    fixture tells the two apart. Conference comes from the club-season, not
    from the match row: x5 is an interconference fixture and has no single
    conference of its own.
    """
    build_standings(con, matches(TWO_CONFERENCE_MATCHES), clubs(TWO_CONFERENCE_CLUBS))
    second_round = standings_on(con, "2024-03-09")
    assert len(second_round) == 8
    leaders = sorted(club for club, row in second_round.items() if row["rank_before"] == 1)
    assert leaders == ["club_a", "club_e"]
    assert second_round["club_a"]["conference"] == "East"
    assert second_round["club_e"]["conference"] == "West"
    assert {row["n_clubs"] for row in second_round.values()} == {4}
    # the drawn clubs share second in each conference; the losers are fourth
    assert second_round["club_c"]["rank_before"] == 2
    assert second_round["club_d"]["rank_before"] == 2
    assert second_round["club_b"]["rank_before"] == 4
    assert second_round["club_f"]["rank_before"] == 4
    # and each conference gets its own snapshot row: East finishes a 4, b 3, d 2, c 1
    assert [r[0] for r in final_table(con, 2024, "East")] == [
        "club_a",
        "club_b",
        "club_d",
        "club_c",
    ]
    assert [r[0] for r in final_table(con, 2024, "West")][0] == "club_e"


def test_clubs_not_playing_on_a_date_still_have_a_row(
    con: duckdb.DuckDBPyConnection, tiny_clubs: pd.DataFrame
) -> None:
    """The full field is ranked on every date any club in the conference plays.

    On a Wednesday when only two of four clubs play, the other two carry their
    table forward with is_match_date = false, so rank_before is the position a
    fan would recognise rather than "rank among clubs in action".
    """
    season = matches(
        [
            ("w1", 2024, "2024-03-02", "club_a", "club_b", 2, 0, 5000),
            ("w2", 2024, "2024-03-02", "club_c", "club_d", 1, 1, 4000),
            ("w3", 2024, "2024-03-06", "club_b", "club_c", 0, 1, 4500),  # a midweek pair
            ("w4", 2024, "2024-03-09", "club_a", "club_c", 0, 0, 5500),
            ("w5", 2024, "2024-03-09", "club_d", "club_b", 1, 0, 3000),
        ]
    )
    build_standings(con, season, tiny_clubs)
    midweek = standings_on(con, "2024-03-06")
    assert {c: r["is_match_date"] for c, r in midweek.items()} == {
        "club_a": False,
        "club_b": True,
        "club_c": True,
        "club_d": False,
    }
    # club_a did not play but is still top on 3 points; club_c and club_d share 2nd on 1
    assert midweek["club_a"]["rank_before"] == 1
    assert midweek["club_c"]["rank_before"] == 2
    assert midweek["club_d"]["rank_before"] == 2
    assert midweek["club_b"]["rank_before"] == 4
    # and on the Saturday after, club_c carries the midweek win forward: 4 points
    saturday = standings_on(con, "2024-03-09")
    assert (saturday["club_c"]["pts_before"], saturday["club_c"]["played_before"]) == (4, 2)
    assert saturday["club_c"]["rank_before"] == 1
    assert saturday["club_a"]["rank_before"] == 2


def test_unplayed_fixture_date_is_a_grid_date_with_carried_forward_totals(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A postponed fixture still puts its date on the grid, and adds nothing to the table.

    tiny_season with m3 (club_b v club_c, 03-09) unplayed. 03-09 is still a
    grid date for all four clubs and is a match date for all four - club_b and
    club_c have a fixture that day even though it has no result - with the
    round-one totals carried forward. On 03-16 club_b and club_c have played
    one match to club_a's and club_d's two. Nothing in the fixture list is
    dropped or invented: the snapshot the day after the last round shows
    club_a 7 (3 played), club_b 3 (2), club_c 2 (2), club_d 1 (3).
    """
    build_standings(con, with_unplayed(tiny_season, ["m3"]), tiny_clubs)
    postponed = standings_on(con, "2024-03-09")
    assert {c: r["is_match_date"] for c, r in postponed.items()} == {
        "club_a": True,
        "club_b": True,
        "club_c": True,
        "club_d": True,
    }
    assert {c: r["pts_before"] for c, r in postponed.items()} == {
        "club_a": 3,
        "club_b": 0,
        "club_c": 1,
        "club_d": 1,
    }
    third_round = standings_on(con, "2024-03-16")
    assert {c: r["played_before"] for c, r in third_round.items()} == {
        "club_a": 2,
        "club_b": 1,
        "club_c": 1,
        "club_d": 2,
    }
    assert final_table(con, 2024, "East") == [
        ("club_a", 3, 7, 4, 6, 1),
        ("club_b", 2, 3, -1, 2, 2),
        ("club_c", 2, 2, 0, 2, 3),
        ("club_d", 3, 1, -3, 3, 4),
    ]
    assert no_future_leakage(con).passed


def test_snapshot_follows_the_last_fixture_even_when_it_is_unplayed(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A season in progress: the snapshot sits after the last SCHEDULED date.

    With the third round still to play, the last grid date is 03-16 (a match
    date, both fixtures unplayed) and the snapshot is 03-17. Both carry the
    table as it stands after two rounds - club_a 6, club_c 2, club_d 1,
    club_b 1 - so the current table exists as a row either way.
    """
    build_standings(con, with_unplayed(tiny_season, ["m5", "m6"]), tiny_clubs)
    dates = [
        r[0] for r in con.execute("SELECT DISTINCT date FROM int_standings ORDER BY 1").fetchall()
    ]
    assert dates == [dt.date(2024, 3, d) for d in (2, 9, 16, 17)]
    last_round = standings_on(con, "2024-03-16")
    assert all(r["is_match_date"] for r in last_round.values())
    assert final_table(con, 2024, "East") == [
        ("club_a", 2, 6, 4, 5, 1),
        ("club_c", 2, 2, 0, 1, 2),
        ("club_d", 2, 1, -2, 2, 3),
        ("club_b", 2, 1, -2, 0, 4),
    ]
    assert {c: r["pts_before"] for c, r in last_round.items()} == {
        "club_a": 6,
        "club_b": 1,
        "club_c": 2,
        "club_d": 1,
    }


def test_standings_start_from_zero_each_season(con: duckdb.DuckDBPyConnection) -> None:
    """The window and the ASOF join are both bounded by season.

    Two seasons of the same two clubs. club_a ends 2023 on 4 points and opens
    2024 on 0; without the season bound on the ASOF join the 2024 opener would
    inherit the 2023 table. Each season gets its own snapshot.
    """
    season = matches(
        [
            ("s1", 2023, "2023-03-04", "club_a", "club_b", 1, 0, 4800),
            ("s2", 2023, "2023-03-11", "club_b", "club_a", 0, 0, 3900),
            ("s3", 2024, "2024-03-02", "club_a", "club_b", 0, 0, 5000),
            ("s4", 2024, "2024-03-09", "club_b", "club_a", 1, 0, 4000),
        ]
    )
    club_rows = clubs(
        [
            ("club_a", 2023, "East", "Club A"),
            ("club_b", 2023, "East", "Club B"),
            ("club_a", 2024, "East", "Club A"),
            ("club_b", 2024, "East", "Club B"),
        ]
    )
    build_standings(con, season, club_rows)
    opener = standings_on(con, "2024-03-02")
    assert {c: (r["played_before"], r["pts_before"]) for c, r in opener.items()} == {
        "club_a": (0, 0),
        "club_b": (0, 0),
    }
    assert final_table(con, 2023, "East") == [("club_a", 2, 4, 1, 1, 1), ("club_b", 2, 1, -1, 0, 2)]
    assert final_table(con, 2024, "East") == [("club_b", 2, 4, 1, 1, 1), ("club_a", 2, 1, -1, 0, 2)]
    assert no_future_leakage(con).passed
    assert no_future_leakage(con).metadata["rows_checked"] == 12  # 2 clubs x 2 seasons x 3


def test_club_season_missing_from_stg_clubs_is_named_by_the_check(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A club-season absent from club_conference must not silently vanish.

    int_standings joins the conference with an inner join on purpose, so a
    missing club-season would drop out of the table with no error. The staging
    check all_club_seasons_have_conference is what makes that loud, naming the
    exact (club_id, season) pair to add.
    """
    three_clubs = tiny_clubs[tiny_clubs["club_id"] != "club_d"]
    stage_frames(con, tiny_season, three_clubs)
    result = all_club_seasons_have_conference(con)
    assert not result.passed
    assert result.metadata["missing"] == [{"club_id": "club_d", "season": 2024}]
    assert "club_conference.csv" in result.metadata["hint"]
    # and the inner join really would have dropped club_d: the check is load-bearing
    runner.materialise(con, "int_standings")
    present = {r[0] for r in con.execute("SELECT DISTINCT club_id FROM int_standings").fetchall()}
    assert present == {"club_a", "club_b", "club_c"}
    # the full fixture passes
    stage_frames(con, tiny_season, tiny_clubs)
    assert all_club_seasons_have_conference(con).passed


def test_doubleheader_is_caught_before_it_corrupts_the_window(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A club appearing twice on one date makes the standings window ambiguous.

    The window orders by date alone, so two matches on one date have an
    arbitrary frame boundary between them, and the mart's join to the standings
    fans out. Neither failure raises; one_match_per_club_per_date does.
    """
    doubled = pd.concat(
        [
            tiny_season,
            matches([("m7", 2024, "2024-03-02", "club_a", "club_c", 1, 0, 4800)]),
        ],
        ignore_index=True,
    )
    stage_frames(con, doubled, tiny_clubs)
    result = one_match_per_club_per_date(con)
    assert not result.passed
    assert result.metadata["n_club_dates"] == 2  # club_a and club_c both play twice
    assert result.metadata["club_dates"][0] == {
        "season": 2024,
        "club_id": "club_a",
        "date": "2024-03-02",
        "matches": 2,
    }
    stage_frames(con, tiny_season, tiny_clubs)
    assert one_match_per_club_per_date(con).passed


# ---------------------------------------------------------------------------
# The real season
# ---------------------------------------------------------------------------


def test_example_season_reproduces_the_published_final_table(
    con: duckdb.DuckDBPyConnection, example_archive_path: Path
) -> None:
    """The whole layer on EPL 2018/19, no network, real reference CSVs.

    The arbiter is a pure-Python recompute of the table from the payload's goal
    counts: every club's points and goal difference are pinned to it. The
    published record is then asserted on top, first the top six and bottom
    three that a previous build verified, then the full finishing order, which
    the recompute agreed with row for row.
    """
    assert_reference_csvs_parse(con)
    assert load_example_raw(con, example_archive_path) == 380

    ctx = new_run_context()
    ensure_log_tables(con)
    counts = runner.run_sql_layer(con, ctx)
    assert counts["stg_matches"] == 380
    assert counts["stg_clubs"] == 20
    assert counts["mart_match_features"] == 380

    unmapped = con.execute(
        "SELECT count(*) FROM stg_matches WHERE home_club_id IS NULL OR away_club_id IS NULL"
    ).fetchone()
    assert unmapped == (0,)

    logged = con.execute(
        "SELECT check_name, passed FROM check_log WHERE run_id = ? ORDER BY check_name",
        [ctx.run_id],
    ).fetchall()
    every_check = STAGING_CHECKS + INTERMEDIATE_CHECKS + MART_CHECKS
    assert sorted(name for name, _ in logged) == sorted(c.__name__ for c in every_check)
    assert all(passed for _, passed in logged), logged

    # the snapshot row: 20 clubs, 38 played each, the day after the last fixture
    table = final_table(con, 2018, "Premier League")
    assert len(table) == 20
    assert {row[1] for row in table} == {38}
    assert [row[5] for row in table] == list(range(1, 21))  # no ties in the real table
    snapshot_date = con.execute("SELECT max(date) FROM int_standings").fetchone()
    assert snapshot_date == (dt.date(2019, 5, 13),)

    # 1. the Python recompute is the arbiter: points, goal difference, goals for
    expected = python_final_table(example_archive_path)
    assert set(expected) == {row[0] for row in table}
    for club, _played, pts, gd, gf, _rank in table:
        assert (pts, gd, gf) == expected[club], club

    # 2. the published record, on top of the recompute
    computed = {club: (pts, gd) for club, _, pts, gd, _, _ in table}
    for club, pts, gd in PUBLISHED_2018_19[:6] + PUBLISHED_2018_19[-3:]:
        assert computed[club] == (pts, gd), club
    assert [(club, pts, gd) for club, _, pts, gd, _, _ in table] == PUBLISHED_2018_19


def test_example_season_features_and_decay_curve(
    con: duckdb.DuckDBPyConnection, example_archive_path: Path
) -> None:
    """The mart and the decay curve on the real season.

    Three clubs were relegated and, with the top four as the playoff line,
    most of the league is out of that race by spring - so the curve has rows.
    Every feature outside config.ALLOWED_NULL_FEATURES is populated on all 380
    rows, and exactly twenty rows - each club's first home match - have no
    last_home_gate.
    """
    assert_reference_csvs_parse(con)
    load_example_raw(con, example_archive_path)
    runner.run_sql_layer(con)

    curve = con.execute(
        "SELECT matches_since_elimination, n, n_club_seasons FROM mart_decay_curve ORDER BY 1"
    ).fetchall()
    assert curve, "no eliminated-club home matches found"
    assert curve[0][0] == 0
    assert all(n >= 1 and n_cs >= 1 for _, n, n_cs in curve)

    strict = [f for f in all_features() if f not in config.ALLOWED_NULL_FEATURES]
    for feature in strict:
        nulls = con.execute(
            f'SELECT count(*) FROM mart_match_features WHERE "{feature}" IS NULL'
        ).fetchone()
        assert nulls == (0,), feature

    lag_nulls = con.execute(
        "SELECT count(*) FROM mart_match_features WHERE last_home_gate IS NULL"
    ).fetchone()
    assert lag_nulls == (20,)
    played = con.execute(
        "SELECT count(*) FROM mart_match_features WHERE is_played AND attendance IS NOT NULL"
    ).fetchone()
    assert played == (380,)
    openers = con.execute(
        """
        SELECT count(*) FILTER (WHERE is_season_opener),
               count(*) FILTER (WHERE is_final_home_match)
        FROM mart_match_features
        """
    ).fetchone()
    assert openers == (20, 20)

    # matches_since_elimination by its literal definition - the count of the
    # club's earlier home fixtures in the season on or after eliminated_on,
    # -1 while live - recomputed with a correlated subquery rather than the
    # mart's window, and compared row for row.
    mismatched = con.execute(
        """
        SELECT m.match_id, m.matches_since_elimination, expected
        FROM (
            SELECT m.match_id, m.matches_since_elimination,
                   CASE WHEN k.eliminated_on IS NULL OR m.date < k.eliminated_on THEN -1
                        ELSE (SELECT count(*) FROM stg_matches e
                              WHERE e.home_club_id = m.home_club_id AND e.season = m.season
                                AND e.date < m.date AND e.date >= k.eliminated_on)
                   END AS expected
            FROM mart_match_features m
            JOIN int_stakes k
              ON k.club_id = m.home_club_id AND k.season = m.season AND k.date = m.date
        ) m
        WHERE matches_since_elimination IS DISTINCT FROM expected
        """
    ).fetchall()
    assert mismatched == []
    eliminated_rows = con.execute(
        "SELECT count(*) FROM mart_match_features WHERE matches_since_elimination >= 0"
    ).fetchone()
    assert eliminated_rows is not None and eliminated_rows[0] > 0
