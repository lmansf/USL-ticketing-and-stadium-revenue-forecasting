"""Data quality checks, one plain function per check.

Plain functions returning a result object, not assertions scattered through the
transform code. Two reasons: every result gets logged whether it passed or
failed, and the same function body becomes a Dagster asset check in phase two
with only a decorator change. An assertion cannot make that trip.

See docs/phases/05-sql-layer.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb


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


def matches_are_fresh(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when the latest match is stale during the season.

    The naive version of this check fires every week in January, and a check that
    cries wolf in the off-season is a check people mute. Gate on
    config.in_season() so an eighty-day gap in the winter reads as correct.

    This is the check that catches the silent Tuesday: the run succeeded, nothing
    new landed, and the dashboard is quietly showing last week.

    Args:
        con: Open connection.

    Returns:
        CheckResult with latest_match and age_days in metadata.

    TODO: implement. See docs/phases/02-duckdb-and-the-lock-problem.md,
    exercise 2.1.
    """
    raise NotImplementedError("TODO: see docs/phases/02-duckdb-and-the-lock-problem.md")


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

    TODO: implement. See docs/phases/03-club-name-consistency.md, exercise 3.1.
    """
    raise NotImplementedError("TODO: see docs/phases/03-club-name-consistency.md")


def row_count_preserved(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when staging has fewer rows than raw.

    A second, independent signal to all_clubs_mapped. That check only catches
    NULLs; a mapping that silently points two different clubs at one club_id
    produces no nulls at all, and this catches it.

    Args:
        con: Open connection.

    Returns:
        CheckResult with both counts in metadata.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/03-club-name-consistency.md")


def one_row_per_match(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when match_id is not unique in staging.

    Args:
        con: Open connection.

    Returns:
        CheckResult with any duplicated match_ids in metadata.

    TODO: implement.
    """
    raise NotImplementedError("TODO")


def no_future_leakage(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when int_standings uses a result on or after the row's own date.

    The one check that catches the mistake no other check would find. Point-in-
    time correctness does not announce itself when it breaks - it shows up as
    suspiciously good validation error, which is easy to mistake for success.

    Args:
        con: Open connection.

    Returns:
        CheckResult with offending rows in metadata.

    TODO: implement. One approach: recompute pts_before for a sample of rows from
    matches strictly before the date and compare. See
    docs/phases/04-standings-as-of-match-date.md.
    """
    raise NotImplementedError("TODO: see docs/phases/04-standings-as-of-match-date.md")


def features_not_null(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when a model feature contains nulls outside the allowed set.

    Some nulls are correct: a club's first ever home match has no last_home_gate.
    Which nulls are legitimate is a decision you make once and encode in
    config.ALLOWED_NULL_FEATURES. This is demo scenario D4, and the demo is about
    being able to explain the choice.

    Args:
        con: Open connection.

    Returns:
        CheckResult with per-column null counts in metadata.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/07-two-models.md#handling-nulls")


def mart_matches_staging(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """Fail when the mart lost matches relative to playable staging rows.

    Args:
        con: Open connection.

    Returns:
        CheckResult with both counts in metadata.

    TODO: implement. "Playable" means a match with a result - decide whether
    unplayed future fixtures belong in the mart, since predictions need them.
    """
    raise NotImplementedError("TODO")


STAGING_CHECKS = (
    matches_are_fresh,
    all_clubs_mapped,
    row_count_preserved,
    one_row_per_match,
)
INTERMEDIATE_CHECKS = (no_future_leakage,)
MART_CHECKS = (features_not_null, mart_matches_staging)
