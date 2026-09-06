"""Reference tables: the hand-maintained CSVs and the config values SQL needs.

The SQL files are static, so tunables such as the COVID window and the match
timezone reach them through a one-row table, ref_config, rather than through
string formatting. One place builds that table - here - so the runner and the
test fixtures cannot disagree about its columns.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from usl import config

# Every CSV under usl/ref/ is read with every column as VARCHAR. Typing is the
# SQL layer's job, and a provider id must stay a string so that 93 and "93"
# cannot become different join keys.
REFERENCE_CSVS: dict[str, Path] = {
    "club_aliases": config.CLUB_ALIASES_CSV,
    "club_conference": config.CLUB_CONFERENCE_CSV,
    "conference_structure": config.CONFERENCE_STRUCTURE_CSV,
    "derbies": config.DERBIES_CSV,
    "stadiums": config.STADIUMS_CSV,
}


def normalize_club_key(raw: object) -> str:
    """Canonical string form of a club identifier, provider id or name.

    Applied on both sides of the alias join and in the loader that reads the
    CSV, so the two cannot drift. Whitespace collapse only - anything more
    aggressive (dropping "FC", stripping accents, fuzzy matching) collides
    distinct clubs, and a collision is the silent failure phase 03 exists to
    prevent. See docs/phases/03-club-name-consistency.md, exercise 3.2.

    A whole-number float is an id that came through pandas (93.0 is 93); a
    fractional one is a coordinate and keeps its digits.

    Args:
        raw: A provider id (int, float, or str) or a display name.

    Returns:
        The key as it appears in club_aliases.raw_name.
    """
    if isinstance(raw, bool):
        return str(raw)
    if isinstance(raw, float) and not raw.is_integer():
        # a coordinate in stadiums.csv, not an id: keep every digit
        return repr(raw)
    if isinstance(raw, int | float):
        return str(int(raw))
    return " ".join(str(raw).split())


# The same normalisation, in SQL, for the join side. Keep the two in step.
NORMALIZE_SQL = "trim(regexp_replace(CAST({col} AS VARCHAR), '\\s+', ' ', 'g'))"


def create_ref_config(con: duckdb.DuckDBPyConnection) -> None:
    """Materialise ref_config, the one-row table of config values SQL reads.

    Args:
        con: Open connection with write access.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE ref_config AS
        SELECT
            CAST(? AS VARCHAR)  AS match_tz,
            CAST(? AS DATE)     AS covid_start,
            CAST(? AS DATE)     AS covid_end,
            CAST(? AS INTEGER)  AS assumed_relegation_spots,
            CAST(? AS INTEGER)  AS default_playoff_spots,
            CAST(? AS VARCHAR)  AS void_statuses
        """,
        [
            config.MATCH_TZ,
            config.COVID_START.isoformat(),
            config.COVID_END.isoformat(),
            config.ASSUMED_RELEGATION_SPOTS,
            config.DEFAULT_PLAYOFF_SPOTS,
            ",".join(s.lower() for s in config.VOID_MATCH_STATUSES),
        ],
    )


def register_reference_frame(con: duckdb.DuckDBPyConnection, name: str, df: pd.DataFrame) -> None:
    """Materialise a reference table from a frame, every column as VARCHAR.

    Used by the tests and the demos to stand a small reference table up without
    a CSV on disk. The runner uses read_reference_csv for the real files.

    Args:
        con: Open connection with write access.
        name: Table name.
        df: The rows. Column names are kept as-is.
    """
    frame = df.copy()
    for col in frame.columns:
        frame[col] = frame[col].map(lambda v: None if pd.isna(v) else normalize_club_key(v))
    con.register("_ref_frame", frame)
    con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _ref_frame')
    con.unregister("_ref_frame")


def read_reference_csv(con: duckdb.DuckDBPyConnection, name: str, path: Path) -> int:
    """Materialise one hand-maintained CSV as a table, every column as VARCHAR.

    Args:
        con: Open connection with write access.
        name: Table name.
        path: The CSV.

    Returns:
        Row count.

    Raises:
        FileNotFoundError: The CSV is missing. These files are code; a missing
            one is a broken checkout, not an empty dataset.
    """
    if not path.exists():
        raise FileNotFoundError(f"reference file missing: {path}")
    con.execute(
        f'CREATE OR REPLACE TABLE "{name}" AS '
        "SELECT * FROM read_csv(?, header = true, all_varchar = true)",
        [str(path)],
    )
    # Normalise the join keys the same way the loader and the SQL do.
    cols = [r[0] for r in con.execute(f'DESCRIBE "{name}"').fetchall()]
    for col in cols:
        con.execute(f'UPDATE "{name}" SET "{col}" = ' + NORMALIZE_SQL.format(col=f'"{col}"'))
    row = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()
    return int(row[0]) if row else 0
