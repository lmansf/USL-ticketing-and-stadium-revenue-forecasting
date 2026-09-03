"""Write Tableau extracts.

Not a fallback you write if there is time. This is what your workbook falls back
to on day 15 when the Tableau Desktop trial expires, and it is what makes this
repo useful to someone who has no Tableau at all. Write it before you start the
trial, not after.

See docs/phases/08-tableau.md#fallback-export
"""

from __future__ import annotations

from pathlib import Path

import duckdb


def export_csv(con: duckdb.DuckDBPyConnection, table: str, out_dir: Path) -> Path:
    """Write one table to CSV.

    Args:
        con: Open connection.
        table: Table name.
        out_dir: Destination directory, created if absent.

    Returns:
        Path written.

    TODO: implement.
    """
    raise NotImplementedError("TODO")


def export_hyper(con: duckdb.DuckDBPyConnection, table: str, out_dir: Path) -> Path:
    """Write one table to a Tableau Hyper extract via pantab.

    Optional. CSV works everywhere; Hyper is faster to open and preserves types,
    which matters mostly for dates that Tableau would otherwise guess at.

    Args:
        con: Open connection.
        table: Table name.
        out_dir: Destination directory.

    Returns:
        Path written.

    Raises:
        ImportError: If pantab is not installed. Say so plainly and point at the
            CSV path rather than failing the run - the optional dependency should
            not be able to break Tuesday.

    TODO: implement, or delete if you stay on CSV.
    """
    raise NotImplementedError("TODO")


def export_all(con: duckdb.DuckDBPyConnection, out_dir: Path | None = None) -> list[Path]:
    """Export every table Tableau needs.

    Args:
        con: Open connection.
        out_dir: Defaults to config.EXTRACT_DIR.

    Returns:
        Paths written, for the run log.

    TODO: implement over config.EXTRACT_TABLES. Export what Tableau needs rather
    than everything - raw_matches has no place in a dashboard.
    """
    raise NotImplementedError("TODO")
