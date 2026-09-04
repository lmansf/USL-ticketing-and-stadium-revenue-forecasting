"""Load raw matches into DuckDB, idempotently.

Re-running Tuesday's job twice must not double any club's attendance. This works
correctly from day one - it is not a demo failure. See
docs/phases/09-break-and-fix.md, "Demonstrate working, do not break".

See docs/phases/01-ingest-to-raw.md
"""

from __future__ import annotations

import duckdb
import pandas as pd

from usl.logging_setup import LoadStats

RAW_MATCHES_DDL = """
CREATE TABLE IF NOT EXISTS raw_matches (
    match_id    VARCHAR PRIMARY KEY,
    season      INTEGER,
    date        VARCHAR,
    home_raw    VARCHAR,
    away_raw    VARCHAR,
    score       VARCHAR,
    attendance  VARCHAR,
    scraped_at  TIMESTAMP,
    source_url  VARCHAR
);
"""
# Note the VARCHAR columns for date, score, and attendance. Raw means raw - the
# source's representation, uncoerced. Typing happens in stg_matches where it is
# reviewable and re-runnable. If the source ever emits "n/a" in the attendance
# column, this table holds it and staging decides what to do about it.


def ensure_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create raw_matches if it does not exist.

    Args:
        con: Open connection with write access.

    TODO: implement using RAW_MATCHES_DDL.
    """
    raise NotImplementedError("TODO")


def upsert_matches(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> LoadStats:
    """Insert new matches and update existing ones, returning the split.

    Upsert, not insert. Attendance figures get corrected by sources after the
    fact, so updating on conflict is right - you want the latest figure, not the
    first one you saw.

    The returned split is the evidence that the guard works. A bare row count is
    unchanged by a bug that overwrites every row with garbage; inserted /
    updated / unchanged is not. Log all three every run.

    Args:
        con: Open connection with write access.
        df: Parsed matches with a match_id column.

    Returns:
        LoadStats with the insert/update/unchanged split.

    TODO: implement. See docs/phases/01-ingest-to-raw.md, exercise 1.2. DuckDB's
    ON CONFLICT does not hand you the split, so compute it against the existing
    keys before the write.
    """
    raise NotImplementedError("TODO: see docs/phases/01-ingest-to-raw.md, exercise 1.2")


def backfill(con: duckdb.DuckDBPyConnection, seasons: list[int]) -> LoadStats:
    """Scrape and load every listed season.

    A one-time operation of a few thousand rows. Sleep between requests.

    Args:
        con: Open connection with write access.
        seasons: Season years to load.

    Returns:
        Aggregate LoadStats across all seasons.

    TODO: implement. Log per season as you go - a backfill that fails on season
    seven should tell you it got through six.
    """
    raise NotImplementedError("TODO")
