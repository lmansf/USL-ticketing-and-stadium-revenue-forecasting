"""Load raw matches into DuckDB, idempotently.

Re-running Tuesday's job twice must not double any club's attendance. This works
correctly from day one - it is not a demo failure. See
docs/phases/09-break-and-fix.md, "Demonstrate working, do not break".

See docs/phases/01-ingest-to-raw.md
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any

import duckdb
import pandas as pd

from usl.db import row_count, table_exists
from usl.ingest.footystats import add_match_id, fetch_season_matches, parse_season_matches
from usl.logging_setup import LoadStats

log = logging.getLogger(__name__)

# Raw means raw. Every value is the source's own representation, uncoerced -
# goals and attendance as text, kick-off as the unix seconds the API sent,
# the season as the string "2018/2019". Typing happens in stg_matches where it
# is reviewable and re-runnable. If the source ever emits "N/A" in attendance,
# this table holds it and staging decides what to do about it.
#
# The lifted columns are the handful staging needs, renamed to snake_case
# (naming, not typing). The full match record - all two hundred fields the API
# sends, including the ones nobody has a use for yet - travels alongside in
# raw_json, so a field discarded today is not a field lost tomorrow.
RAW_MATCHES_DDL = """
CREATE TABLE IF NOT EXISTS raw_matches (
    match_id        VARCHAR PRIMARY KEY,   -- 'fs:' + the provider's match id
    provider_id     VARCHAR,               -- the provider's match id, as text
    season_id       INTEGER,               -- the FootyStats season id requested
    season_raw      VARCHAR,               -- e.g. '2018/2019', exactly as returned
    date_unix       BIGINT,                -- kick-off, unix seconds, as returned
    status          VARCHAR,               -- 'complete', 'incomplete', ... as returned
    game_week       VARCHAR,
    home_raw        VARCHAR,               -- provider club id (homeID), as text
    away_raw        VARCHAR,
    home_name       VARCHAR,               -- the provider's CURRENT name for the club
    away_name       VARCHAR,
    home_goals      VARCHAR,               -- homeGoalCount, uncoerced
    away_goals      VARCHAR,
    attendance      VARCHAR,               -- uncoerced. Staging decides what 0 / -1 / '' mean
    stadium_name    VARCHAR,
    raw_json        VARCHAR,               -- the whole match record, byte-faithful
    ingested_at     TIMESTAMP,
    source_endpoint VARCHAR
);
"""

# API field -> raw column, for the fields that get their own column. Naming
# only; values are stored as sent. A parsed frame may arrive with either the
# API names (straight from parse_season_matches) or these names (a fixture).
RAW_COLUMN_MAP: dict[str, str] = {
    "id": "provider_id",
    "season": "season_raw",
    "date_unix": "date_unix",
    "status": "status",
    "game_week": "game_week",
    "homeID": "home_raw",
    "awayID": "away_raw",
    "home_name": "home_name",
    "away_name": "away_name",
    "homeGoalCount": "home_goals",
    "awayGoalCount": "away_goals",
    "attendance": "attendance",
    "stadium_name": "stadium_name",
}

RAW_COLUMNS: tuple[str, ...] = (
    "match_id",
    "provider_id",
    "season_id",
    "season_raw",
    "date_unix",
    "status",
    "game_week",
    "home_raw",
    "away_raw",
    "home_name",
    "away_name",
    "home_goals",
    "away_goals",
    "attendance",
    "stadium_name",
    "raw_json",
    "ingested_at",
    "source_endpoint",
)

# A row whose key already exists counts as UPDATED when any of these differ
# from what is stored, and UNCHANGED otherwise. ingested_at is deliberately
# not in the list - it changes every run and would make every row "updated".
CONTENT_COLUMNS: tuple[str, ...] = (
    "date_unix",
    "status",
    "home_goals",
    "away_goals",
    "attendance",
)


def ensure_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create raw_matches if it does not exist.

    Args:
        con: Open connection with write access.
    """
    con.execute(RAW_MATCHES_DDL)


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
        df: Parsed matches with a match_id column. Columns may carry the API
            names (RAW_COLUMN_MAP keys) or the raw table names; anything not in
            RAW_COLUMNS is ignored, anything missing is NULL.

    Returns:
        LoadStats with the insert/update/unchanged split.

    Raises:
        ValueError: No match_id column, a null match_id, or a frame that
            carries the same column under both its API and its raw name.
    """
    ensure_raw_tables(con)
    batch = _conform(df)
    types = _column_types(con)
    cast = {col: f'CAST(b."{col}" AS {types[col]})' for col in RAW_COLUMNS}
    # IS DISTINCT FROM is never null, so "changed" and "not changed" partition
    # the existing keys exactly and the three counts add up to the batch.
    changed = " OR ".join(f'{cast[col]} IS DISTINCT FROM r."{col}"' for col in CONTENT_COLUMNS)

    con.register(_BATCH_VIEW, batch)
    try:
        row = con.execute(
            f"""
            SELECT
                count(*) FILTER (WHERE r.match_id IS NULL)                         AS inserted,
                count(*) FILTER (WHERE r.match_id IS NOT NULL AND ({changed}))     AS updated,
                count(*) FILTER (WHERE r.match_id IS NOT NULL AND NOT ({changed})) AS unchanged
            FROM {_BATCH_VIEW} b
            LEFT JOIN raw_matches r USING (match_id)
            """
        ).fetchone()
        stats = (
            LoadStats(inserted=int(row[0]), updated=int(row[1]), unchanged=int(row[2]))
            if row
            else LoadStats()
        )

        columns = ", ".join(f'"{col}"' for col in RAW_COLUMNS)
        select = ", ".join(f'{cast[col]} AS "{col}"' for col in RAW_COLUMNS)
        updates = ", ".join(
            f'"{col}" = excluded."{col}"' for col in RAW_COLUMNS if col != "match_id"
        )
        con.execute(
            f"INSERT INTO raw_matches ({columns}) SELECT {select} FROM {_BATCH_VIEW} b "
            f"ON CONFLICT (match_id) DO UPDATE SET {updates}"
        )
    finally:
        con.unregister(_BATCH_VIEW)

    log.info(
        "raw_matches inserted=%s updated=%s unchanged=%s total_in_table=%s",
        stats.inserted,
        stats.updated,
        stats.unchanged,
        row_count(con, "raw_matches"),
    )
    return stats


def load_season(
    con: duckdb.DuckDBPyConnection, season_id: int, *, force: bool = False
) -> LoadStats:
    """Fetch, parse, key, and upsert one season.

    Served from data/raw_archive/ when the response is there, which after the
    subscription lapses is always.

    Args:
        con: Open connection with write access.
        season_id: FootyStats season id.
        force: Re-request the season even when it is archived.

    Returns:
        The insert/update/unchanged split for this season.
    """
    ensure_raw_tables(con)
    payload = fetch_season_matches(season_id, force=force)
    df = add_match_id(parse_season_matches(payload, season_id))
    log.info("season_id %s: loading %d row(s) into raw_matches", season_id, len(df))
    return upsert_matches(con, df)


def backfill(con: duckdb.DuckDBPyConnection, seasons: list[int]) -> LoadStats:
    """Ingest and load every listed season.

    Served from data/raw_archive/ where possible, so re-running is free and
    works with no API key.

    Seasons are loaded in the order given and the log says which ones finished
    before a failure, so a backfill that dies on season seven tells you it got
    through six. The failure is re-raised, never swallowed: a partial backfill
    that reports success is how a mart ends up quietly missing a season.

    Args:
        con: Open connection with write access.
        seasons: FootyStats season ids to load, from usl/ref/seasons.csv.

    Returns:
        Aggregate LoadStats across all seasons.
    """
    ensure_raw_tables(con)
    total = LoadStats()
    completed: list[int] = []
    for season_id in seasons:
        try:
            stats = load_season(con, season_id)
        except Exception:
            log.error(
                "backfill failed on season_id %s; completed before it: %s",
                season_id,
                completed if completed else "none",
            )
            raise
        log.info(
            "season_id %s: inserted=%s updated=%s unchanged=%s",
            season_id,
            stats.inserted,
            stats.updated,
            stats.unchanged,
        )
        total = total + stats
        completed.append(season_id)
    log.info(
        "backfill complete: %d season(s), inserted=%s updated=%s unchanged=%s",
        len(completed),
        total.inserted,
        total.updated,
        total.unchanged,
    )
    return total


def raw_summary(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """What raw_matches holds, in the shape the run log records.

    Args:
        con: Open connection.

    Returns:
        rows: row count. seasons: sorted season years, read from the first
        four digits of season_raw ('2018/2019' -> 2018). max_match_date: the
        UTC date of the latest kick-off, or None. null_attendance_pct: the
        percentage of rows with no usable gate figure - null, empty, not a
        number, or at most zero, the same test the coverage script applies -
        rounded to two places, 0.0 for an empty table.
    """
    if not table_exists(con, "raw_matches"):
        return {"rows": 0, "seasons": [], "max_match_date": None, "null_attendance_pct": 0.0}

    row = con.execute(
        """
        SELECT count(*),
               max(date_unix),
               count(*) FILTER (WHERE coalesce(TRY_CAST(attendance AS DOUBLE), 0) <= 0)
        FROM raw_matches
        """
    ).fetchone()
    rows, max_unix, unusable = (int(row[0]), row[1], int(row[2])) if row else (0, None, 0)

    seasons = [
        int(found[0])
        for found in con.execute(
            """
            SELECT DISTINCT TRY_CAST(substr(season_raw, 1, 4) AS INTEGER) AS season
            FROM raw_matches
            WHERE season_raw IS NOT NULL AND regexp_matches(season_raw, '^[0-9]{4}')
            ORDER BY season
            """
        ).fetchall()
        if found[0] is not None
    ]

    max_match_date = (
        dt.datetime.fromtimestamp(int(max_unix), tz=dt.UTC).date() if max_unix is not None else None
    )
    return {
        "rows": rows,
        "seasons": seasons,
        "max_match_date": max_match_date,
        "null_attendance_pct": round(100.0 * unusable / rows, 2) if rows else 0.0,
    }


# --------------------------------------------------------------------------
# Private helpers
# --------------------------------------------------------------------------

_BATCH_VIEW = "_raw_batch"


def _as_text(value: object) -> str | None:
    """One raw value as the text the table stores, or None for a missing one.

    Raw means raw, so this is rendering, not typing: an int is written as its
    digits, a string as itself, nothing is parsed. The one deliberate
    adjustment is an integral float, which is what pandas makes of an int
    column with a gap in it: 74439.0 is written as 74439, so the same figure
    compares equal across loads whether or not some other row was missing.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, int):
        return str(value)
    if value is pd.NA or value is pd.NaT:
        return None
    return str(value)


def _as_int(value: object) -> int | None:
    """season_id as an int, or None when missing."""
    text = _as_text(value)
    return None if text is None else int(text)


def _conform(df: pd.DataFrame) -> pd.DataFrame:
    """The batch in raw_matches shape: renamed, deduplicated, every value text.

    Returns a new frame with a fresh index and object-dtype columns, so DuckDB
    sees plain Python values whatever dtypes the caller's frame carried.
    """
    if "match_id" not in df.columns:
        raise ValueError("upsert_matches needs a match_id column - run add_match_id first")

    renames = {
        api: raw
        for api, raw in RAW_COLUMN_MAP.items()
        if api != raw and api in df.columns and raw not in df.columns
    }
    frame = df.rename(columns=renames)
    duplicated = sorted(set(frame.columns[frame.columns.duplicated()]))
    if duplicated:
        raise ValueError(f"upsert_matches: columns present more than once: {duplicated}")
    if frame["match_id"].isna().any():
        raise ValueError("upsert_matches: match_id is null on some rows")

    before = len(frame)
    frame = frame.drop_duplicates(subset="match_id", keep="last")
    dropped = before - len(frame)
    if dropped:
        log.warning(
            "raw_matches: %d row(s) with a repeated match_id dropped from the batch, last row wins",
            dropped,
        )

    size = len(frame)
    data: dict[str, Any] = {}
    for col in RAW_COLUMNS:
        values = list(frame[col]) if col in frame.columns else [None] * size
        if col == "ingested_at":
            stamped = pd.to_datetime(pd.Series(values, dtype=object), utc=True)
            data[col] = stamped.dt.tz_localize(None)
        elif col == "season_id":
            data[col] = pd.Series([_as_int(v) for v in values], dtype=object)
        else:
            data[col] = pd.Series([_as_text(v) for v in values], dtype=object)
    return pd.DataFrame(data)


def _column_types(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Column -> DuckDB type of raw_matches, read from the table itself.

    The casts in the load follow the table that exists rather than a copy of
    the DDL, so the two cannot drift apart.
    """
    rel = con.table("raw_matches")
    return {name: str(kind) for name, kind in zip(rel.columns, rel.types, strict=True)}
