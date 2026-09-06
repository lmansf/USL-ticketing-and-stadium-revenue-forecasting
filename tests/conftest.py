"""Shared test fixtures.

Small, hand-built frames rather than samples of real data. A fixture you can
verify by reading it is worth more than a realistic one you cannot.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from usl import config
from usl.transform.reference import create_ref_config, register_reference_frame
from usl.transform.runner import materialise
from usl.weather.schema import ensure_weather_table


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


@pytest.fixture
def tiny_structure() -> pd.DataFrame:
    """conference_structure rows for tiny_season: two playoff spots, one relegation spot."""
    return pd.DataFrame(
        [(2024, "East", 2, 1, "test fixture")],
        columns=["season", "conference", "playoff_spots", "relegation_spots", "note"],
    )


@pytest.fixture
def tiny_derbies() -> pd.DataFrame:
    """One derby pair for tiny_season: club_a against club_b, in either direction."""
    return pd.DataFrame(
        [("club_a", "club_b", "test fixture")],
        columns=["club_id_a", "club_id_b", "note"],
    )


@pytest.fixture
def tiny_raw(tiny_season: pd.DataFrame, club_aliases: pd.DataFrame) -> pd.DataFrame:
    """tiny_season in the shape of raw_matches, with display names as the raw club strings.

    home_raw / away_raw carry 'Club A' style names so the club_aliases fixture
    maps them; the real pipeline carries provider ids there, which is the same
    join. Every value is text, as the raw tier requires.
    """
    display = {row.club_id: row.raw_name for row in club_aliases.itertuples() if row.note == ""}
    rows = []
    for i, m in enumerate(tiny_season.itertuples(), start=1):
        kickoff = dt.datetime.combine(dt.date.fromisoformat(m.date), dt.time(12, 0), tzinfo=dt.UTC)
        record = {
            "id": i,
            "season": str(m.season),
            "date_unix": int(kickoff.timestamp()),
            "status": "complete",
            "homeID": display[m.home_club_id],
            "awayID": display[m.away_club_id],
            "homeGoalCount": m.home_goals,
            "awayGoalCount": m.away_goals,
            "attendance": m.attendance,
        }
        rows.append(
            {
                "match_id": m.match_id,
                "provider_id": str(i),
                "season_id": 999,
                "season_raw": str(m.season),
                "date_unix": int(kickoff.timestamp()),
                "status": "complete",
                "game_week": str((i + 1) // 2),
                "home_raw": display[m.home_club_id],
                "away_raw": display[m.away_club_id],
                "home_name": display[m.home_club_id],
                "away_name": display[m.away_club_id],
                "home_goals": str(m.home_goals),
                "away_goals": str(m.away_goals),
                "attendance": str(m.attendance),
                "stadium_name": f"{display[m.home_club_id]} Ground",
                "raw_json": json.dumps(record),
                "ingested_at": dt.datetime(2024, 3, 19, 6, 0, 0),
                "source_endpoint": "league-matches",
            }
        )
    return pd.DataFrame(rows)


def stage_frames(
    con: duckdb.DuckDBPyConnection,
    matches: pd.DataFrame,
    clubs: pd.DataFrame,
    *,
    structure: pd.DataFrame | None = None,
    derbies: pd.DataFrame | None = None,
    void: list[str] | None = None,
    stadiums: pd.DataFrame | None = None,
    weather: pd.DataFrame | None = None,
) -> None:
    """Stand up the staging tier and reference tables straight from small frames.

    Skips raw entirely, so a standings or features test can materialise
    int_standings, int_stakes, and mart_match_features from tiny_season
    without an alias CSV or an archive on disk.

    Args:
        con: In-memory connection.
        matches: tiny_season-shaped rows. home_goals / away_goals may be None
            for an unplayed fixture; attendance may be None.
        clubs: tiny_clubs-shaped rows (club_id, season, conference, display_name).
        structure: conference_structure rows. Defaults to two playoff spots and
            one relegation spot for every (season, conference) in clubs.
        derbies: derbies rows. Defaults to none.
        void: match_ids to mark is_void (a cancelled fixture). Defaults to none.
        stadiums: stadiums rows (club_id, stadium, lat, lon, valid_from,
            valid_to, note). Defaults to one row per club covering every date.
        weather: raw_weather-shaped rows (club_id, date, weather_source,
            forecast_horizon_days, temperature_2m_max, temperature_2m_min,
            precipitation_sum, wind_speed_10m_max, cloud_cover_mean). Defaults
            to none, so the weather features are null.
    """
    create_ref_config(con)

    con.register("_matches", matches)
    con.execute(
        """
        CREATE OR REPLACE TABLE stg_matches AS
        SELECT
            CAST(match_id AS VARCHAR)                         AS match_id,
            CAST(season AS INTEGER)                           AS season,
            CAST(NULL AS INTEGER)                             AS season_id,
            CAST(date AS DATE)                                AS date,
            CAST(date AS TIMESTAMP) + INTERVAL 12 HOUR        AS kickoff_utc,
            CASE WHEN home_goals IS NULL THEN 'incomplete' ELSE 'complete' END AS status,
            home_goals IS NOT NULL                            AS is_played,
            list_contains($void, CAST(match_id AS VARCHAR))   AS is_void,
            CAST(home_club_id AS VARCHAR)                     AS home_raw,
            CAST(away_club_id AS VARCHAR)                     AS away_raw,
            CAST(home_club_id AS VARCHAR)                     AS home_club_id,
            CAST(away_club_id AS VARCHAR)                     AS away_club_id,
            CAST(home_goals AS INTEGER)                       AS home_goals,
            CAST(away_goals AS INTEGER)                       AS away_goals,
            CAST(attendance AS INTEGER)                       AS attendance,
            CAST(date AS DATE) BETWEEN (SELECT covid_start FROM ref_config)
                                   AND (SELECT covid_end FROM ref_config) AS is_covid_affected,
            dayofweek(CAST(date AS DATE))                     AS day_of_week,
            month(CAST(date AS DATE))                         AS month,
            dayofweek(CAST(date AS DATE)) IN (0, 6)           AS is_weekend,
            dayofweek(CAST(date AS DATE)) IN (2, 3, 4)        AS is_midweek
        FROM _matches
        """,
        {"void": list(void or [])},
    )
    con.unregister("_matches")

    con.register("_clubs", clubs)
    con.execute(
        """
        CREATE OR REPLACE TABLE stg_clubs AS
        SELECT CAST(club_id AS VARCHAR) AS club_id,
               CAST(season AS INTEGER)  AS season,
               CAST(conference AS VARCHAR) AS conference,
               CAST(display_name AS VARCHAR) AS display_name
        FROM _clubs
        """
    )
    con.unregister("_clubs")

    if structure is None:
        structure = (
            clubs[["season", "conference"]]
            .drop_duplicates()
            .assign(playoff_spots=2, relegation_spots=1, note="default test structure")
        )
    register_reference_frame(con, "conference_structure", structure)

    if derbies is None:
        derbies = pd.DataFrame(columns=["club_id_a", "club_id_b", "note"])
    register_reference_frame(con, "derbies", derbies)

    if stadiums is None:
        stadiums = pd.DataFrame(
            [
                (club_id, f"{club_id} ground", 40.0, -80.0, "2000-01-01", "2099-12-31", "test")
                for club_id in sorted(clubs["club_id"].astype(str).unique())
            ],
            columns=["club_id", "stadium", "lat", "lon", "valid_from", "valid_to", "note"],
        )
    register_reference_frame(con, "stadiums", stadiums)

    con.execute("DROP TABLE IF EXISTS raw_weather")
    ensure_weather_table(con)
    if weather is not None and not weather.empty:
        frame = weather.copy()
        frame["lat"] = 40.0
        frame["lon"] = -80.0
        frame["fetched_at"] = dt.datetime(2024, 3, 19, 6, 0, 0)
        frame["source_file"] = "test"
        con.register("_weather", frame)
        con.execute(
            """
            INSERT INTO raw_weather
            SELECT CAST(club_id AS VARCHAR), CAST(date AS DATE), lat, lon,
                   CAST(weather_source AS VARCHAR), CAST(forecast_horizon_days AS INTEGER),
                   temperature_2m_max, temperature_2m_min, precipitation_sum,
                   wind_speed_10m_max, cloud_cover_mean, fetched_at, source_file
            FROM _weather
            """
        )
        con.unregister("_weather")
    materialise(con, "stg_weather")


def with_unplayed(frame: pd.DataFrame, match_ids: list[str]) -> pd.DataFrame:
    """Blank the result and gate of the named fixtures, so they read as unplayed.

    Goals and attendance become nullable integers with pd.NA on the named
    rows; stage_frames then marks them is_played = false with a null
    attendance, which is how a future fixture arrives from the real staging SQL.

    Args:
        frame: tiny_season-shaped rows.
        match_ids: The fixtures to leave unplayed.

    Returns:
        A copy with those rows blanked.
    """
    out = frame.copy()
    unplayed = out["match_id"].isin(match_ids)
    for col in ("home_goals", "away_goals", "attendance"):
        out[col] = pd.array(out[col].tolist(), dtype="Int64")
        out.loc[unplayed, col] = pd.NA
    return out


@pytest.fixture
def example_archive_path() -> Path:
    """The committed example-key response: EPL 2018/19, season id 1625, 380 matches."""
    return config.ARCHIVE_DIR / "league-matches_season_id_1625.json"
