"""Write Tableau extracts.

Not a fallback you write if there is time. This is what your workbook falls back
to on day 15 when the Tableau Desktop trial expires, and it is what makes this
repo useful to someone who has no Tableau at all. Write it before you start the
trial, not after.

One CSV per table in config.EXTRACT_TABLES that exists, optionally a Hyper
file beside each via pantab, plus predictions_with_band.csv: predictions joined
to each run's holdout MAE so the club drill-down can shade a band without doing
the join in Tableau. The band is historical residuals - predicted plus or minus
the run's MAE - and the file says so in every row, because a plus-or-minus-one-
MAE band and an 80% interval look identical on a chart and mean different things.

See docs/phases/08-tableau.md#fallback-export
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd

from usl import config
from usl.db import table_exists

log = logging.getLogger(__name__)

# The joined file, written after the per-table extracts. Not a table, so not in
# config.EXTRACT_TABLES.
BAND_FILE_STEM = "predictions_with_band"

# What the band means, carried on every row so the caption cannot drift from
# the numbers. See docs/phases/08-tableau.md, exercise 8.1.
BAND_LABEL = "predicted +/- holdout MAE (historical residuals)"

# DuckDB types written as their ISO text (2024-03-02, 2024-03-02 12:00:00)
# rather than left to pandas, which would otherwise decide per column whether
# a date shows a time part.
_TEMPORAL_TYPES = ("DATE", "TIMESTAMP")

_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _check_table_name(table: str) -> None:
    if not _PLAIN_NAME.fullmatch(table):
        raise ValueError(f"not a plain table name: {table!r}")


def read_for_export(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    """Run a query and return its rows with dates and timestamps as ISO text.

    Args:
        con: Open connection.
        sql: A SELECT.

    Returns:
        The rows, temporal columns as strings, everything else as DuckDB
        typed it.
    """
    relation = con.sql(sql)
    columns = list(relation.columns)
    types = [str(t).upper() for t in relation.types]
    projection = ", ".join(
        f'CAST("{col}" AS VARCHAR) AS "{col}"'
        if any(sql_type.startswith(prefix) for prefix in _TEMPORAL_TYPES)
        else f'"{col}"'
        for col, sql_type in zip(columns, types, strict=True)
    )
    return con.sql(f"SELECT {projection} FROM ({sql}) AS _export").df()


def export_csv(con: duckdb.DuckDBPyConnection, table: str, out_dir: Path) -> Path:
    """Write one table to <out_dir>/<table>.csv.

    No index column, UTF-8, dates and timestamps as ISO text. Tableau Public
    reads this without any type hints.

    Args:
        con: Open connection.
        table: Table name.
        out_dir: Destination directory, created if absent.

    Returns:
        Path written.
    """
    _check_table_name(table)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = read_for_export(con, f'SELECT * FROM "{table}"')
    path = out_dir / f"{table}.csv"
    frame.to_csv(path, index=False, encoding="utf-8")
    log.info("export: wrote %s rows=%d", path, len(frame))
    return path


def export_hyper(con: duckdb.DuckDBPyConnection, table: str, out_dir: Path) -> Path:
    """Write one table to a Tableau Hyper extract via pantab.

    Optional. CSV works everywhere; Hyper is faster to open and preserves types,
    which matters mostly for dates that Tableau would otherwise guess at.

    Args:
        con: Open connection.
        table: Table name.
        out_dir: Destination directory, created if absent.

    Returns:
        Path written.

    Raises:
        ImportError: pantab is not installed. The message names the CSV that
            stands in for the Hyper file. Not swallowed here - export_all is
            where the optional dependency is allowed to degrade the run to
            CSV-only rather than fail it.
    """
    _check_table_name(table)
    out_dir = Path(out_dir)
    csv_path = out_dir / f"{table}.csv"
    try:
        import pantab
    except ImportError as exc:
        raise ImportError(
            f"pantab is not installed, so {table} cannot be written as a Hyper extract. "
            f"Use the CSV at {csv_path} instead - Tableau Public reads it directly - or "
            "install the optional dependency with: pip install 'usl-attendance[tableau]'"
        ) from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = con.sql(f'SELECT * FROM "{table}"').df()
    path = out_dir / f"{table}.hyper"
    pantab.frame_to_hyper(frame, path, table=table)
    log.info("export: wrote %s rows=%d", path, len(frame))
    return path


def export_all(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path | None = None,
    *,
    hyper: bool = False,
) -> list[Path]:
    """Export every table Tableau needs, plus predictions_with_band.csv.

    Tables in config.EXTRACT_TABLES that do not exist yet are skipped with an
    INFO line, never an error: on the first run the model tables are not there
    until train has run, and an export that fails on that would make the weekly
    command order-sensitive for no benefit.

    Args:
        con: Open connection.
        out_dir: Defaults to config.EXTRACT_DIR.
        hyper: Also write a .hyper beside each CSV. When pantab is missing the
            first failure is logged as a warning and the rest of the run is
            CSV-only; the CSVs are always written.

    Returns:
        Paths written, for the run log.
    """
    destination = Path(out_dir) if out_dir is not None else config.EXTRACT_DIR
    destination.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    write_hyper = hyper
    for table in config.EXTRACT_TABLES:
        if not table_exists(con, table):
            log.info("export: table %s does not exist yet, skipped", table)
            continue
        paths.append(export_csv(con, table, destination))
        if write_hyper:
            try:
                paths.append(export_hyper(con, table, destination))
            except ImportError as exc:
                log.warning("export: Hyper extracts skipped for this run - %s", exc)
                write_hyper = False

    if table_exists(con, "predictions") and table_exists(con, "model_metrics"):
        paths.append(export_predictions_with_band(con, destination))
    else:
        log.info(
            "export: %s.csv skipped - predictions and model_metrics are not both present yet",
            BAND_FILE_STEM,
        )
    log.info("export: %d file(s) written to %s", len(paths), destination)
    return paths


def export_predictions_with_band(con: duckdb.DuckDBPyConnection, out_dir: Path) -> Path:
    """Write predictions joined to each run's holdout MAE as an uncertainty band.

    Columns: match_id, model_name, run_date, predicted, actual, mae, band_low,
    band_high, band_label. The band is predicted plus or minus the MAE of the
    same model on the same run_date, from model_metrics; band_label says so on
    every row. The same width everywhere is the honest weakness of the choice,
    and the label is what stops it being read as a confidence interval.

    Args:
        con: Open connection. predictions and model_metrics must exist.
        out_dir: Destination directory, created if absent.

    Returns:
        Path written.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    label = BAND_LABEL.replace("'", "''")
    frame = read_for_export(
        con,
        f"""
        SELECT
            p.match_id,
            p.model_name,
            p.run_date,
            p.predicted,
            p.actual,
            m.mae,
            p.predicted - m.mae AS band_low,
            p.predicted + m.mae AS band_high,
            '{label}' AS band_label
        FROM predictions p
        JOIN model_metrics m
          ON m.model_name = p.model_name AND m.run_date = p.run_date
        ORDER BY p.run_date, p.model_name, p.match_id
        """,
    )
    path = out_dir / f"{BAND_FILE_STEM}.csv"
    frame.to_csv(path, index=False, encoding="utf-8")
    log.info("export: wrote %s rows=%d", path, len(frame))
    return path
