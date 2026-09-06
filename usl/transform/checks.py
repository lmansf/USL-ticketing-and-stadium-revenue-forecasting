"""Data quality checks, one plain function per check.

Plain functions returning a result object, not assertions scattered through the
transform code. Two reasons: every result gets logged whether it passed or
failed, and the same function body becomes a Dagster asset check in phase two
with only a decorator change. An assertion cannot make that trip.

Every check takes an open connection and returns a CheckResult whose metadata
is JSON-serialisable (dates as strings), because the runner writes it to
check_log as JSON and puts it in the CheckFailure message.

See docs/phases/05-sql-layer.md
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import duckdb

from usl import config
from usl.db import row_count, table_exists
from usl.features.definitions import all_features


class CheckFailure(RuntimeError):
    """One or more checks failed in a tier."""


@dataclass
class CheckResult:
    """The outcome of one check.

    Attributes:
        name: Check identifier, stable across runs so it can be tracked over time.
        tier: 'staging', 'intermediate', or 'mart'.
        passed: Whether the check passed.
        metadata: Check-specific detail. Recorded as JSON in check_log, so keep
            it to JSON-serialisable values.
    """

    name: str
    tier: str
    passed: bool
    metadata: dict[str, Any] = field(default_factory=dict)


Check = Callable[[duckdb.DuckDBPyConnection], CheckResult]

# How many offending rows a check lists. Enough to see the pattern, not so many
# that the failure message and the check_log row become unreadable.
_LIST_LIMIT = 10

# The unpivot every standings-shaped recomputation starts from. Kept in one
# place so the leakage check and nothing else has to restate it.
_CLUB_MATCH_POINTS_SQL = """
    SELECT season, date, home_club_id AS club_id,
           CASE WHEN home_goals > away_goals THEN 3
                WHEN home_goals = away_goals THEN 1 ELSE 0 END AS points,
           home_goals - away_goals AS gd,
           home_goals AS gf
    FROM stg_matches WHERE is_played
    UNION ALL
    SELECT season, date, away_club_id,
           CASE WHEN away_goals > home_goals THEN 3
                WHEN away_goals = home_goals THEN 1 ELSE 0 END,
           away_goals - home_goals,
           away_goals
    FROM stg_matches WHERE is_played
"""


def matches_are_fresh(
    con: duckdb.DuckDBPyConnection, *, today: dt.date | None = None
) -> CheckResult:
    """Fail when the latest match of the current season is stale during the season.

    The naive version of this check fires every week in January, and a check that
    cries wolf in the off-season is a check people mute. Gate on
    config.in_season() so an eighty-day gap in the winter reads as correct.

    This is the check that catches the silent Tuesday: the run succeeded, nothing
    new landed, and the dashboard is quietly showing last week.

    With config.CURRENT_SEASON unset the data is archive-only: nothing could be
    fresh, and the check passes recording that reason rather than failing every
    run for ever.

    Args:
        con: Open connection.
        today: The date to measure age from. Defaults to today; tests pin it.

    Returns:
        CheckResult with latest_match, age_days, and in_season in metadata.
    """
    if config.CURRENT_SEASON is None:
        return CheckResult(
            "matches_are_fresh",
            "staging",
            True,
            {"reason": "archive-only: no current season configured"},
        )
    on = today or dt.date.today()
    row = con.execute(
        "SELECT max(date) FROM stg_matches WHERE is_played AND season = ?",
        [config.CURRENT_SEASON],
    ).fetchone()
    latest: dt.date | None = row[0] if row else None
    in_season = config.in_season(on)
    age_days = (on - latest).days if latest is not None else None
    # No played match at all in the current season is stale by definition.
    fresh = age_days is not None and age_days <= config.MAX_MATCH_AGE_DAYS
    return CheckResult(
        "matches_are_fresh",
        "staging",
        fresh or not in_season,
        {
            "current_season": config.CURRENT_SEASON,
            "latest_match": str(latest) if latest is not None else None,
            "age_days": age_days,
            "max_age_days": config.MAX_MATCH_AGE_DAYS,
            "in_season": in_season,
        },
    )


def all_clubs_mapped(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when any club string did not map to a canonical club_id.

    The failure message must name the exact unmapped strings, so fixing it is a
    paste into club_aliases.csv rather than an investigation.

    This is why the staging join is a LEFT JOIN and not an INNER JOIN. The inner
    join drops the rows and tells you nothing.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the unmapped names in metadata.
    """
    rows = con.execute(
        """
        SELECT DISTINCT home_raw AS name FROM stg_matches WHERE home_club_id IS NULL
        UNION
        SELECT DISTINCT away_raw FROM stg_matches WHERE away_club_id IS NULL
        """
    ).fetchall()
    unmapped = sorted("<null>" if r[0] is None else str(r[0]) for r in rows)
    metadata: dict[str, Any] = {"n_unmapped": len(unmapped), "unmapped": unmapped}
    # An alias row with the raw_name filled in and the club_id left blank joins
    # and yields a null club_id. Naming it as "unmapped" would send the reader
    # to add a second row for the same string, which then fans the join out.
    blank: list[str] = []
    if table_exists(con, "club_aliases"):
        blank = sorted(
            str(r[0])
            for r in con.execute(
                "SELECT raw_name FROM club_aliases WHERE raw_name IS NOT NULL AND club_id IS NULL"
            ).fetchall()
        )
    if blank:
        metadata["blank_club_id"] = blank
    if unmapped:
        still_missing = [name for name in unmapped if name not in blank]
        hints = []
        if still_missing:
            hints.append(
                "add each string to usl/ref/club_aliases.csv as a raw_name row. If one "
                "looks like an id that IS in the CSV, suspect a type mismatch rather than "
                "a missing club: 93 and '93' are different join keys."
            )
        if blank:
            hints.append(
                f"{blank} are already in club_aliases.csv with a blank club_id - fill the "
                "club_id in on that row rather than adding another."
            )
        metadata["hint"] = " ".join(hints)
    return CheckResult("all_clubs_mapped", "staging", not unmapped, metadata)


def row_count_preserved(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when the staging row count differs from the raw row count.

    A second, independent signal to all_clubs_mapped. That check only catches
    NULLs; a duplicated alias row fans the join out and a botched join drops
    rows, and neither produces a null. Equal is the only correct answer.

    Args:
        con: Open connection.

    Returns:
        CheckResult with both counts in metadata.
    """
    if not table_exists(con, "raw_matches"):
        return CheckResult(
            "row_count_preserved",
            "staging",
            False,
            {"reason": "raw_matches does not exist - nothing has been loaded"},
        )
    raw = row_count(con, "raw_matches")
    staging = row_count(con, "stg_matches")
    return CheckResult(
        "row_count_preserved",
        "staging",
        raw == staging,
        {"raw_rows": raw, "staging_rows": staging, "difference": staging - raw},
    )


def one_row_per_match(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when match_id is not unique in staging.

    Args:
        con: Open connection.

    Returns:
        CheckResult with any duplicated match_ids in metadata.
    """
    rows = con.execute(
        """
        SELECT match_id, count(*) AS n FROM stg_matches
        GROUP BY match_id HAVING count(*) > 1 ORDER BY match_id
        """
    ).fetchall()
    return CheckResult(
        "one_row_per_match",
        "staging",
        not rows,
        {
            "n_duplicated": len(rows),
            "duplicates": [{"match_id": r[0], "rows": int(r[1])} for r in rows[:_LIST_LIMIT]],
        },
    )


def one_match_per_club_per_date(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a club appears in more than one match on one date.

    The standings window orders by date alone, so a club with two matches on one
    date has an arbitrary frame boundary between them, and the join from the mart
    to the standings fans out. Both failures are silent. A doubleheader, a season
    ingested twice, or two raw names collapsed onto one club_id all produce it.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the offending (season, club_id, date) triples in metadata.
    """
    rows = con.execute(
        """
        WITH club_dates AS (
            SELECT season, date, home_club_id AS club_id FROM stg_matches
            UNION ALL
            SELECT season, date, away_club_id FROM stg_matches
        )
        SELECT season, club_id, date, count(*) AS n
        FROM club_dates
        WHERE club_id IS NOT NULL
        GROUP BY season, club_id, date HAVING count(*) > 1
        ORDER BY season, date, club_id
        """
    ).fetchall()
    return CheckResult(
        "one_match_per_club_per_date",
        "staging",
        not rows,
        {
            "n_club_dates": len(rows),
            "club_dates": [
                {"season": r[0], "club_id": r[1], "date": str(r[2]), "matches": int(r[3])}
                for r in rows[:_LIST_LIMIT]
            ],
        },
    )


def all_club_seasons_have_conference(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a club-season in stg_matches has no usable row in stg_clubs.

    int_standings joins conference from stg_clubs with an inner join, on
    purpose, so a club-season missing from club_conference.csv would otherwise
    drop out of the table with no error. This check is what makes that loud. A
    row whose conference is blank counts as missing: the grid is built per
    conference and NULL matches nothing, so the club would vanish just the
    same. Unmapped clubs (null club_id) are all_clubs_mapped's job and are
    skipped.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the missing (club_id, season) pairs in metadata.
    """
    rows = con.execute(
        """
        WITH club_seasons AS (
            SELECT DISTINCT season, home_club_id AS club_id FROM stg_matches
            WHERE home_club_id IS NOT NULL
            UNION
            SELECT DISTINCT season, away_club_id FROM stg_matches
            WHERE away_club_id IS NOT NULL
        )
        SELECT cs.club_id, cs.season
        FROM club_seasons cs
        LEFT JOIN stg_clubs c
          ON c.club_id = cs.club_id AND c.season = cs.season AND c.conference IS NOT NULL
        WHERE c.club_id IS NULL
        ORDER BY cs.season, cs.club_id
        """
    ).fetchall()
    return CheckResult(
        "all_club_seasons_have_conference",
        "staging",
        not rows,
        {
            "n_missing": len(rows),
            "missing": [{"club_id": r[0], "season": r[1]} for r in rows[:_LIST_LIMIT]],
            **(
                {
                    "hint": (
                        "add a row per (club_id, season) to usl/ref/club_conference.csv, "
                        "with the conference filled in - a row with a blank conference "
                        "counts as missing, because the standings grid would drop it"
                    )
                }
                if rows
                else {}
            ),
        },
    )


def one_conference_per_club_season(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a (club_id, season) appears more than once in stg_clubs.

    A duplicated row in club_conference.csv produces no null anywhere, so
    all_clubs_mapped and all_club_seasons_have_conference both pass. What it
    does instead is fan out: the standings grid gets the club twice (n_clubs
    inflated, the playoff and relegation lines shifted one place) and the
    mart's join to int_standings doubles that club's matches, which
    mart_matches_staging would report as a bare count two tiers later without
    naming the cause. This names it, at the tier where it can be fixed with
    one line in the CSV.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the duplicated (club_id, season) pairs and the
        conferences they were given in metadata.
    """
    rows = con.execute(
        """
        SELECT club_id, season, count(*) AS n, list(DISTINCT conference ORDER BY conference)
        FROM stg_clubs
        GROUP BY club_id, season
        HAVING count(*) > 1
        ORDER BY season, club_id
        """
    ).fetchall()
    return CheckResult(
        "one_conference_per_club_season",
        "staging",
        not rows,
        {
            "n_duplicated": len(rows),
            "duplicated": [
                {
                    "club_id": r[0],
                    "season": r[1],
                    "rows": int(r[2]),
                    "conferences": [str(c) for c in r[3]],
                }
                for r in rows[:_LIST_LIMIT]
            ],
            **(
                {"hint": "one row per (club_id, season) in usl/ref/club_conference.csv"}
                if rows
                else {}
            ),
        },
    )


def played_rows_consistent(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a status value would quietly un-play matches.

    is_played is derived from the status string, and goals and attendance are
    nulled for anything that is not played. So a provider that renamed
    'complete' to 'finished' would strip every result and every gate out of
    staging while every other check passed and the run log reported success.
    Two signals catch it at the staging tier: a raw row that carries parseable
    goals and a positive attendance but reads is_played = false, and any status
    value outside config.KNOWN_MATCH_STATUSES - which is how the set gets
    extended deliberately rather than by accident.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the inconsistent statuses and the unknown statuses,
        each with a row count, in metadata.
    """
    if not table_exists(con, "raw_matches"):
        return CheckResult(
            "played_rows_consistent",
            "staging",
            True,
            {"reason": "no raw_matches table; staging was built without a raw tier"},
        )
    known = [status.lower() for status in config.KNOWN_MATCH_STATUSES]
    inconsistent = con.execute(
        """
        SELECT COALESCE(r.status, '<null>') AS status, count(*)
        FROM raw_matches r
        JOIN stg_matches s USING (match_id)
        WHERE NOT s.is_played
          AND TRY_CAST(r.home_goals AS INTEGER) IS NOT NULL
          AND TRY_CAST(r.away_goals AS INTEGER) IS NOT NULL
          AND TRY_CAST(r.attendance AS INTEGER) > 0
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    unknown = con.execute(
        """
        SELECT COALESCE(status, '<null>') AS status, count(*)
        FROM raw_matches
        WHERE status IS NULL OR NOT list_contains($known, lower(trim(status)))
        GROUP BY 1
        ORDER BY 1
        """,
        {"known": known},
    ).fetchall()
    metadata: dict[str, Any] = {
        "inconsistent_statuses": {str(r[0]): int(r[1]) for r in inconsistent},
        "unknown_statuses": {str(r[0]): int(r[1]) for r in unknown},
        "known_statuses": known,
    }
    if inconsistent or unknown:
        metadata["hint"] = (
            "a played match is reading as unplayed, or the provider sent a status this "
            "project has never seen. If a status was renamed, add it to "
            "config.KNOWN_MATCH_STATUSES (and to VOID_MATCH_STATUSES if it means the fixture "
            "will never be played); if it means played, teach stg_matches.sql about it."
        )
    return CheckResult(
        "played_rows_consistent", "staging", not inconsistent and not unknown, metadata
    )


def all_conference_clubs_have_fixtures(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a club-season in stg_clubs has no fixture in a conference that does.

    The mirror image of all_club_seasons_have_conference. The standings grid
    is built from stg_clubs, so a club-season that exists only in
    club_conference.csv - a club pasted under the wrong season, or listed for a
    season it folded before - sits in the table on zero points on every date:
    n_clubs is one too many, the relegation line moves up a place, and a real
    club on zero points with a negative goal difference is out-ranked by a
    phantom. No null appears anywhere. This names the pair.

    A (season, conference) with no fixture at all is different: it is reference
    data written ahead of the data, which is how the subscription month works
    (the USL rows are in club_conference.csv before a USL match is archived).
    The grid takes its dates from fixtures, so a fixtureless conference-season
    has no standings rows and cannot move any line. Those are reported in
    metadata, not failed.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the fixture-less (club_id, season) pairs in metadata,
        and the conference-seasons that have no fixtures at all.
    """
    rows = con.execute(
        """
        WITH fixtures AS (
            SELECT DISTINCT season, home_club_id AS club_id FROM stg_matches
            UNION
            SELECT DISTINCT season, away_club_id FROM stg_matches
        ),
        live AS (
            -- conference-seasons in which at least one club has a fixture
            SELECT DISTINCT c.season, c.conference
            FROM fixtures f
            JOIN stg_clubs c ON c.club_id = f.club_id AND c.season = f.season
        )
        SELECT c.club_id, c.season, c.conference
        FROM stg_clubs c
        JOIN live l ON l.season = c.season AND l.conference = c.conference
        LEFT JOIN fixtures f ON f.club_id = c.club_id AND f.season = c.season
        WHERE f.club_id IS NULL
        ORDER BY c.season, c.club_id
        """
    ).fetchall()
    ahead = con.execute(
        """
        WITH fixtures AS (
            SELECT DISTINCT season, home_club_id AS club_id FROM stg_matches
            UNION
            SELECT DISTINCT season, away_club_id FROM stg_matches
        )
        SELECT c.season, c.conference, count(*) AS n_clubs
        FROM stg_clubs c
        LEFT JOIN fixtures f ON f.club_id = c.club_id AND f.season = c.season
        GROUP BY c.season, c.conference
        HAVING count(f.club_id) = 0
        ORDER BY c.season, c.conference
        """
    ).fetchall()
    # An unmapped club string leaves its fixtures with a null club_id, so the
    # club-season it belongs to looks fixtureless here too. That is a cascade
    # of all_clubs_mapped, not a phantom row; say so, so the hint is not wrong.
    unmapped_row = con.execute(
        "SELECT count(*) FROM stg_matches WHERE home_club_id IS NULL OR away_club_id IS NULL"
    ).fetchone()
    n_unmapped = int(unmapped_row[0]) if unmapped_row else 0
    if rows and n_unmapped:
        hint = (
            f"{n_unmapped} fixture(s) carry an unmapped club string, so fix all_clubs_mapped "
            "first: a club whose raw name is missing from usl/ref/club_aliases.csv has no "
            "fixtures here. Only a pair still listed after that is a phantom in "
            "usl/ref/club_conference.csv"
        )
    else:
        hint = (
            "remove the row from usl/ref/club_conference.csv, or check the "
            "season: a club-season with no fixtures is a phantom in the table"
        )
    return CheckResult(
        "all_conference_clubs_have_fixtures",
        "staging",
        not rows,
        {
            "n_without_fixtures": len(rows),
            "without_fixtures": [
                {"club_id": r[0], "season": r[1], "conference": r[2]} for r in rows[:_LIST_LIMIT]
            ],
            "n_unmapped_fixtures": n_unmapped,
            "n_conference_seasons_without_fixtures": len(ahead),
            "conference_seasons_without_fixtures": [
                {"season": r[0], "conference": r[1], "n_clubs": r[2]} for r in ahead[:_LIST_LIMIT]
            ],
            **({"hint": hint} if rows else {}),
        },
    )


def conference_membership_is_plausible(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a club plays more fixtures against other conferences than its own.

    The one error in club_conference.csv the other checks cannot see. A club
    filed under the wrong conference for a season is present, mapped, has
    fixtures, and appears once - and is ranked against the wrong field on every
    date, with the lines of both conferences moved by one club. No null
    appears anywhere.

    The schedule gives it away. A conference exists because its clubs mostly
    play each other, so the club-season whose fixtures are mostly against the
    other conference is the one filed wrongly. Strictly more than half, so a
    balanced schedule never fires. A season with a single conference passes
    trivially. Fixtures against an unmapped or unfiled club are not counted;
    the checks before this one name those.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the implausible club-seasons and their fixture split.
    """
    rows = con.execute(
        """
        WITH sides AS (
            SELECT season, home_club_id AS club_id, away_club_id AS other
            FROM stg_matches WHERE NOT is_void
            UNION ALL
            SELECT season, away_club_id, home_club_id
            FROM stg_matches WHERE NOT is_void
        ),
        split AS (
            SELECT s.season, s.club_id, c.conference,
                   count(*) FILTER (WHERE o.conference = c.conference)  AS same_conference,
                   count(*) FILTER (WHERE o.conference <> c.conference) AS other_conference
            FROM sides s
            JOIN stg_clubs c ON c.club_id = s.club_id AND c.season = s.season
            JOIN stg_clubs o ON o.club_id = s.other AND o.season = s.season
            GROUP BY s.season, s.club_id, c.conference
        )
        SELECT season, club_id, conference, same_conference, other_conference
        FROM split
        WHERE other_conference > same_conference
        ORDER BY season, conference, club_id
        """
    ).fetchall()
    return CheckResult(
        "conference_membership_is_plausible",
        "staging",
        not rows,
        {
            "n_implausible": len(rows),
            "implausible": [
                {
                    "season": r[0],
                    "club_id": r[1],
                    "conference": r[2],
                    "fixtures_in_conference": r[3],
                    "fixtures_outside": r[4],
                }
                for r in rows[:_LIST_LIMIT]
            ],
            **(
                {
                    "hint": (
                        "check the club's conference for that season in "
                        "usl/ref/club_conference.csv against the published table (the "
                        "archived league-tables response): a club filed under the wrong "
                        "conference is ranked against the wrong field and nothing goes null. "
                        "If the schedule genuinely was mostly cross-conference that season, "
                        "the exception belongs in this check, not in the CSV"
                    )
                }
                if rows
                else {}
            ),
        },
    )


def home_matches_resolve_to_one_stadium(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a home match matches no stadiums.csv row, or more than one.

    The weather join is on the club and the validity range that covers the
    match date (phase two, exercise 12.1). Overlapping ranges would duplicate
    the match in an inner join and a gap between ranges would drop it, both
    silently; the join is a LEFT JOIN and this check asserts exactly one. A
    club with no stadium row at all gets no weather and is named here too,
    because a whole club with null weather is the same silent failure at a
    larger grain. Scoped to club-seasons in stg_clubs: a club missing from
    club_conference.csv is already named by all_club_seasons_have_conference,
    and naming it twice would send the reader to the wrong file.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the offending (club_id, date) pairs and clubs.
    """
    if not table_exists(con, "stadiums"):
        return CheckResult(
            "home_matches_resolve_to_one_stadium",
            "staging",
            False,
            {"reason": "stadiums table missing - the reference tables were not loaded"},
        )
    rows = con.execute(
        """
        WITH home AS (
            SELECT m.home_club_id AS club_id, m.date
            FROM stg_matches m
            JOIN stg_clubs c ON c.club_id = m.home_club_id AND c.season = m.season
            WHERE NOT m.is_void
        ),
        matched AS (
            SELECT h.club_id, h.date, count(s.club_id) AS n_rows
            FROM home h
            LEFT JOIN stadiums s
              ON s.club_id = h.club_id
             AND h.date BETWEEN TRY_CAST(s.valid_from AS DATE) AND TRY_CAST(s.valid_to AS DATE)
            GROUP BY h.club_id, h.date
        )
        SELECT club_id, date, n_rows FROM matched WHERE n_rows <> 1
        ORDER BY club_id, date
        """
    ).fetchall()
    no_row = sorted({r[0] for r in rows if r[2] == 0})
    overlapping = [{"club_id": r[0], "date": str(r[1]), "rows": r[2]} for r in rows if r[2] > 1]
    unresolved = [{"club_id": r[0], "date": str(r[1])} for r in rows if r[2] == 0]
    metadata: dict[str, Any] = {
        "n_unresolved": len(unresolved),
        "unresolved": unresolved[:_LIST_LIMIT],
        "clubs_without_a_stadium": no_row[:_LIST_LIMIT],
        "n_overlapping": len(overlapping),
        "overlapping": overlapping[:_LIST_LIMIT],
    }
    if rows:
        hints = []
        if unresolved:
            hints.append(
                "add a usl/ref/stadiums.csv row for each club named, or close the gap between "
                "its validity ranges - a home match on a date no range covers gets no weather"
            )
        if overlapping:
            hints.append(
                "two stadiums.csv rows for the same club cover the same date: end the earlier "
                "range the day before the later one starts"
            )
        metadata["hint"] = ". ".join(hints)
    return CheckResult("home_matches_resolve_to_one_stadium", "staging", not rows, metadata)


def played_weather_is_observed(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a played match old enough for the archive still carries forecast weather.

    A forecast fetched before the match must be replaced by the observation
    afterwards, or the training data quietly contains predictions. The
    archive trails real time, so a match younger than
    config.WEATHER_ARCHIVE_LAG_DAYS is allowed its forecast for now. Played
    matches with no weather at all are counted, not failed: that is the
    state before the backfill is archived, and with weather disabled.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the stale rows and the count of played rows without
        weather.
    """
    on = dt.date.today()
    cutoff = on - dt.timedelta(days=config.WEATHER_ARCHIVE_LAG_DAYS)
    stale = con.execute(
        """
        SELECT match_id, home_club_id, date
        FROM mart_match_features
        WHERE is_played AND weather_source = 'forecast' AND date <= ?
        ORDER BY date, match_id
        """,
        [cutoff],
    ).fetchall()
    counts = con.execute(
        """
        SELECT
            count(*) FILTER (WHERE is_played AND weather_source IS NULL),
            count(*) FILTER (WHERE is_played AND weather_source = 'archive'),
            count(*) FILTER (WHERE NOT is_played AND weather_source = 'forecast')
        FROM mart_match_features
        """
    ).fetchone() or (0, 0, 0)
    metadata: dict[str, Any] = {
        "n_stale_forecasts": len(stale),
        "stale_forecasts": [
            {"match_id": r[0], "club_id": r[1], "date": str(r[2])} for r in stale[:_LIST_LIMIT]
        ],
        "played_without_weather": int(counts[0]),
        "played_observed": int(counts[1]),
        "fixtures_forecast": int(counts[2]),
        "archive_lag_days": config.WEATHER_ARCHIVE_LAG_DAYS,
    }
    if stale:
        metadata["hint"] = (
            "run 'python -m usl.run weather' with USL_WEATHER_ENABLED=1: the archive "
            "should have these dates by now, and the refresh replaces forecast rows with "
            "observations"
        )
    return CheckResult("played_weather_is_observed", "mart", not stale, metadata)


def conference_structure_is_well_formed(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when conference_structure.csv cannot mean what it says.

    Three ways the file goes wrong silently: a (season, conference) pasted
    twice fans int_stakes and the mart out; a non-numeric playoff_spots or
    relegation_spots is TRY_CAST to NULL and quietly takes the configured
    default, which is indistinguishable from leaving it blank on purpose; a
    number of spots at or beyond the size of the conference leaves no line
    club, so the stakes features go null two tiers later without naming the
    row. Blank stays the documented "use the default" signal.

    Args:
        con: Open connection.

    Returns:
        CheckResult with duplicated pairs, unparseable rows, and out-of-range
        rows in metadata.
    """
    if not table_exists(con, "conference_structure"):
        return CheckResult(
            "conference_structure_is_well_formed",
            "staging",
            True,
            {"reason": "no conference_structure table"},
        )
    duplicated = con.execute(
        """
        SELECT season, conference, count(*) FROM conference_structure
        GROUP BY 1, 2 HAVING count(*) > 1 ORDER BY 1, 2
        """
    ).fetchall()
    unparseable = con.execute(
        """
        SELECT season, conference, playoff_spots, relegation_spots FROM conference_structure
        WHERE (playoff_spots IS NOT NULL AND TRY_CAST(playoff_spots AS INTEGER) IS NULL)
           OR (relegation_spots IS NOT NULL AND TRY_CAST(relegation_spots AS INTEGER) IS NULL)
           OR TRY_CAST(season AS INTEGER) IS NULL
        ORDER BY 1, 2
        """
    ).fetchall()
    out_of_range = con.execute(
        """
        WITH sizes AS (
            SELECT season, conference, count(*) AS n_clubs FROM stg_clubs GROUP BY 1, 2
        )
        SELECT s.season, s.conference, cs.playoff_spots, cs.relegation_spots, s.n_clubs
        FROM conference_structure cs
        JOIN sizes s
          ON s.season = TRY_CAST(cs.season AS INTEGER) AND s.conference = cs.conference
        WHERE TRY_CAST(cs.playoff_spots AS INTEGER) NOT BETWEEN 1 AND s.n_clubs - 1
           OR TRY_CAST(cs.relegation_spots AS INTEGER) NOT BETWEEN 0 AND s.n_clubs - 1
        ORDER BY 1, 2
        """
    ).fetchall()
    problems = bool(duplicated or unparseable or out_of_range)
    return CheckResult(
        "conference_structure_is_well_formed",
        "staging",
        not problems,
        {
            "duplicated": [
                {"season": r[0], "conference": r[1], "rows": int(r[2])} for r in duplicated
            ],
            "unparseable": [
                {
                    "season": r[0],
                    "conference": r[1],
                    "playoff_spots": r[2],
                    "relegation_spots": r[3],
                }
                for r in unparseable
            ],
            "out_of_range": [
                {
                    "season": r[0],
                    "conference": r[1],
                    "playoff_spots": r[2],
                    "relegation_spots": r[3],
                    "n_clubs": int(r[4]),
                }
                for r in out_of_range
            ],
            **(
                {
                    "hint": (
                        "one row per (season, conference) in usl/ref/conference_structure.csv, "
                        "spots as plain integers between 1 and the conference size minus one "
                        "(relegation may be 0), or blank to take the configured default"
                    )
                }
                if problems
                else {}
            ),
        },
    )


def derby_clubs_are_known(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when derbies.csv names a club_id that exists in no season.

    A typo in either column, or a stale id after a rename, never matches: the
    pair's is_derby is false for every meeting and nothing says so. is_derby is
    a measured feature in both models, so the silence matters.

    Args:
        con: Open connection.

    Returns:
        CheckResult with the unknown club ids in metadata.
    """
    if not table_exists(con, "derbies"):
        return CheckResult("derby_clubs_are_known", "staging", True, {"reason": "no derbies table"})
    rows = con.execute(
        """
        WITH named AS (
            SELECT club_id_a AS club_id FROM derbies
            UNION
            SELECT club_id_b FROM derbies
        )
        SELECT club_id FROM named
        WHERE club_id IS NOT NULL
          AND club_id NOT IN (SELECT DISTINCT club_id FROM stg_clubs WHERE club_id IS NOT NULL)
        ORDER BY 1
        """
    ).fetchall()
    unknown = [str(r[0]) for r in rows]
    return CheckResult(
        "derby_clubs_are_known",
        "staging",
        not unknown,
        {
            "n_unknown": len(unknown),
            "unknown": unknown,
            **(
                {"hint": "every club_id in usl/ref/derbies.csv must appear in club_conference.csv"}
                if unknown
                else {}
            ),
        },
    )


def no_future_leakage(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when int_standings uses a result on or after the row's own date.

    The one check that catches the mistake no other check would find. Point-in-
    time correctness does not announce itself when it breaks - it shows up as
    suspiciously good validation error, which is easy to mistake for success.

    Recomputes played_before, pts_before, gd_before and gf_before for EVERY
    int_standings row by a different method - a non-equi join summing the
    club's played matches in that season with a match date strictly before the
    row date - and compares. Any disagreement is a leak or a lost match. All
    four columns, because rank_before is built from all of them and a wrong
    gf_before only moves clubs that are tied on the other two.

    Args:
        con: Open connection.

    Returns:
        CheckResult with rows_checked and the first mismatches in metadata.
    """
    rows_checked = row_count(con, "int_standings")
    rows = con.execute(
        f"""
        WITH club_matches AS ({_CLUB_MATCH_POINTS_SQL}),
        recomputed AS (
            SELECT s.season, s.conference, s.club_id, s.date,
                   s.played_before, s.pts_before, s.gd_before, s.gf_before,
                   COUNT(cm.points)            AS played_expected,
                   COALESCE(SUM(cm.points), 0) AS pts_expected,
                   COALESCE(SUM(cm.gd), 0)     AS gd_expected,
                   COALESCE(SUM(cm.gf), 0)     AS gf_expected
            FROM int_standings s
            LEFT JOIN club_matches cm
              ON cm.club_id = s.club_id
             AND cm.season = s.season
             AND cm.date < s.date
            GROUP BY ALL
        )
        SELECT season, conference, club_id, date,
               played_before, played_expected,
               pts_before, pts_expected,
               gd_before, gd_expected,
               gf_before, gf_expected
        FROM recomputed
        WHERE played_before IS DISTINCT FROM played_expected
           OR pts_before IS DISTINCT FROM pts_expected
           OR gd_before IS DISTINCT FROM gd_expected
           OR gf_before IS DISTINCT FROM gf_expected
        ORDER BY season, date, conference, club_id
        """
    ).fetchall()
    mismatches = [
        {
            "season": r[0],
            "conference": r[1],
            "club_id": r[2],
            "date": str(r[3]),
            "played_before": r[4],
            "played_expected": int(r[5]),
            "pts_before": r[6],
            "pts_expected": int(r[7]),
            "gd_before": r[8],
            "gd_expected": int(r[9]),
            "gf_before": r[10],
            "gf_expected": int(r[11]),
        }
        for r in rows[:_LIST_LIMIT]
    ]
    return CheckResult(
        "no_future_leakage",
        "intermediate",
        not rows,
        {"rows_checked": rows_checked, "n_mismatches": len(rows), "mismatches": mismatches},
    )


def features_not_null(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a model feature contains nulls outside the allowed set.

    Some nulls are correct: a club's first ever home match has no last_home_gate.
    Which nulls are legitimate is a decision you make once and encode in
    config.ALLOWED_NULL_FEATURES. This is demo scenario D4, and the demo is about
    being able to explain the choice.

    Args:
        con: Open connection.

    Returns:
        CheckResult with null counts for the offending columns (null_counts) and
        for the permitted ones (allowed_with_nulls) in metadata.
    """
    features = all_features()
    counts_sql = ", ".join(f'count(*) FILTER (WHERE "{f}" IS NULL)' for f in features)
    row = con.execute(f"SELECT count(*), {counts_sql} FROM mart_match_features").fetchone()
    total = int(row[0]) if row else 0
    nulls = {f: int(n) for f, n in zip(features, row[1:] if row else (), strict=True)}
    offending = {f: n for f, n in nulls.items() if n and f not in config.ALLOWED_NULL_FEATURES}
    allowed = {f: n for f, n in nulls.items() if n and f in config.ALLOWED_NULL_FEATURES}
    return CheckResult(
        "features_not_null",
        "mart",
        not offending,
        {"rows": total, "null_counts": offending, "allowed_with_nulls": allowed},
    )


def mart_matches_staging(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when the mart row count differs from the staging row count.

    Unplayed fixtures are in the mart by design - forecasts for remaining home
    matches need their features - so the mart carries every staging match that
    is not void, one row each. Fewer means a join dropped matches; more means
    one fanned out. Void fixtures (cancelled, never to be played) are counted
    separately and expected to be absent.

    Args:
        con: Open connection.

    Returns:
        CheckResult with both counts in metadata.
    """
    mart = row_count(con, "mart_match_features")
    row = con.execute(
        "SELECT count(*) FILTER (WHERE NOT is_void), count(*) FILTER (WHERE is_void) "
        "FROM stg_matches"
    ).fetchone()
    staging, void = (int(row[0]), int(row[1])) if row else (0, 0)
    return CheckResult(
        "mart_matches_staging",
        "mart",
        mart == staging,
        {
            "mart_rows": mart,
            "staging_rows": staging,
            "void_rows": void,
            "difference": mart - staging,
        },
    )


STAGING_CHECKS: tuple[Check, ...] = (
    matches_are_fresh,
    all_clubs_mapped,
    row_count_preserved,
    one_row_per_match,
    one_match_per_club_per_date,
    all_club_seasons_have_conference,
    one_conference_per_club_season,
    all_conference_clubs_have_fixtures,
    conference_membership_is_plausible,
    conference_structure_is_well_formed,
    derby_clubs_are_known,
    played_rows_consistent,
    home_matches_resolve_to_one_stadium,
)
INTERMEDIATE_CHECKS: tuple[Check, ...] = (no_future_leakage,)
MART_CHECKS: tuple[Check, ...] = (
    features_not_null,
    mart_matches_staging,
    played_weather_is_observed,
)
