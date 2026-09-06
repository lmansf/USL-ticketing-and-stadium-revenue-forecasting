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
    if unmapped:
        metadata["hint"] = (
            "add each string to usl/ref/club_aliases.csv as a raw_name row. If one "
            "looks like an id that IS in the CSV, suspect a type mismatch rather than "
            "a missing club: 93 and '93' are different join keys."
        )
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
    """Fail when a club-season in stg_matches has no row in stg_clubs.

    int_standings joins conference from stg_clubs with an inner join, on
    purpose, so a club-season missing from club_conference.csv would otherwise
    drop out of the table with no error. This check is what makes that loud.
    Unmapped clubs (null club_id) are all_clubs_mapped's job and are skipped.

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
        LEFT JOIN stg_clubs c ON c.club_id = cs.club_id AND c.season = cs.season
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
                {"hint": "add a row per (club_id, season) to usl/ref/club_conference.csv"}
                if rows
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
    matches need their features - so the mart carries every staging match, one
    row each. Fewer means a join dropped matches; more means one fanned out.

    Args:
        con: Open connection.

    Returns:
        CheckResult with both counts in metadata.
    """
    mart = row_count(con, "mart_match_features")
    staging = row_count(con, "stg_matches")
    return CheckResult(
        "mart_matches_staging",
        "mart",
        mart == staging,
        {"mart_rows": mart, "staging_rows": staging, "difference": mart - staging},
    )


STAGING_CHECKS: tuple[Check, ...] = (
    matches_are_fresh,
    all_clubs_mapped,
    row_count_preserved,
    one_row_per_match,
    one_match_per_club_per_date,
    all_club_seasons_have_conference,
)
INTERMEDIATE_CHECKS: tuple[Check, ...] = (no_future_leakage,)
MART_CHECKS: tuple[Check, ...] = (features_not_null, mart_matches_staging)
