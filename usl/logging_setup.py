"""Structured run logging.

Logging is a first-class feature of this pipeline, not an afterthought. A run
that fails silently leaves a dashboard quietly showing last week's numbers, and
people act on it.

Two destinations, one configuration: a dated file under logs/, and rows in the
DuckDB tables run_log and check_log so Tableau can answer "when did this data
last update" from inside the dashboard.

Full field reference: docs/reference/logging-and-run-metadata.md
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import duckdb

RUN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS run_log (
    run_id              VARCHAR,
    stage               VARCHAR,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    status              VARCHAR,
    rows_read           INTEGER,
    rows_inserted       INTEGER,
    rows_updated        INTEGER,
    rows_unchanged      INTEGER,
    seasons             VARCHAR,
    max_match_date      DATE,
    null_attendance_pct DOUBLE,
    error_type          VARCHAR,
    error_message       VARCHAR,
    git_sha             VARCHAR,
    duration_seconds    DOUBLE,
    PRIMARY KEY (run_id, stage)
);
"""

CHECK_LOG_DDL = """
CREATE TABLE IF NOT EXISTS check_log (
    run_id     VARCHAR,
    check_name VARCHAR,
    tier       VARCHAR,
    passed     BOOLEAN,
    metadata   VARCHAR,
    checked_at TIMESTAMP,
    PRIMARY KEY (run_id, check_name)
);
"""


@dataclass
class RunContext:
    """Metadata for one invocation of the pipeline, shared across its stages.

    A single run_id across scrape, transform, train, and export is what lets you
    ask "show me every stage of the run that failed last Tuesday" rather than
    reconstructing it from timestamps.
    """

    run_id: str
    started_at: dt.datetime
    git_sha: str | None = None
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class LoadStats:
    """The insert/update/unchanged split for one load.

    Three fields rather than one total, deliberately. A total is unchanged by a
    bug that overwrites every row with garbage; the split is the evidence that
    the idempotency guard works, and it is what the phase 09 demo points at.
    """

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        """Rows touched by this load."""
        return self.inserted + self.updated + self.unchanged


def configure_logging(level: int = logging.INFO, *, run_id: str | None = None) -> None:
    """Configure the root logger for a run.

    Sets up a console handler and a dated file handler under config.LOG_DIR.
    Call once, at the top of the CLI entry point.

    Args:
        level: Root log level. INFO is the normal narrative; DEBUG adds
            per-request detail and the SQL being executed.
        run_id: Included in the log filename so a run's file is findable from
            its run_log row.

    TODO: implement. Keep the format terse and include the timestamp, level,
    module, and message. Resist WARNING for things that happen every run - a log
    where warnings are normal is a log where warnings are invisible.
    """
    raise NotImplementedError("TODO: see docs/reference/logging-and-run-metadata.md")


def new_run_context() -> RunContext:
    """Start a run: generate a run_id, capture the start time and the git sha.

    The git sha matters more than it looks. Six weeks of run history is only
    interpretable if you know which version of the code produced each row.

    Returns:
        A RunContext to thread through the stages of this invocation.

    TODO: implement. Reading the sha should not fail the run when git is
    unavailable or the working tree is not a repo - degrade to None.
    """
    raise NotImplementedError("TODO: see docs/reference/logging-and-run-metadata.md")


def ensure_log_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create run_log and check_log if they do not exist.

    Args:
        con: An open DuckDB connection with write access.

    TODO: implement using RUN_LOG_DDL and CHECK_LOG_DDL above.
    """
    raise NotImplementedError("TODO: see docs/reference/logging-and-run-metadata.md")


def start_stage(
    con: duckdb.DuckDBPyConnection, ctx: RunContext, stage: str
) -> None:
    """Record that a stage has begun, with status 'running'.

    Writing the row at stage start rather than only at the end is what
    distinguishes "crashed hard" from "never started" - a process killed by the
    OS never gets to write a terminal status, and the leftover 'running' row is
    the evidence.

    Args:
        con: Open DuckDB connection.
        ctx: The run context.
        stage: One of 'scrape', 'transform', 'train', 'export'.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/reference/logging-and-run-metadata.md")


def finish_stage(
    con: duckdb.DuckDBPyConnection,
    ctx: RunContext,
    stage: str,
    *,
    status: str,
    metadata: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    """Record a stage's terminal status and metadata.

    Args:
        con: Open DuckDB connection.
        ctx: The run context.
        stage: The stage being closed out.
        status: 'success' or 'failed'.
        metadata: Stage metadata - row counts, seasons touched, max match date,
            null attendance percentage. See the field reference doc for the full
            list and the reasoning behind each.
        error: The exception, when the stage failed. Its class name and
            (truncated) text are recorded.

    TODO: implement. Update the row written by start_stage rather than inserting
    a second one, and compute duration_seconds here.
    """
    raise NotImplementedError("TODO: see docs/reference/logging-and-run-metadata.md")


def log_check_result(
    con: duckdb.DuckDBPyConnection, ctx: RunContext, result: Any
) -> None:
    """Record one check result, passed or failed.

    Logging passes as well as failures is deliberate. A check that has passed for
    six weeks and starts failing is a signal; a check that only writes a row when
    it fails gives you no baseline to notice that against.

    Args:
        con: Open DuckDB connection.
        ctx: The run context.
        result: A CheckResult from usl.transform.checks.

    TODO: implement. Serialise result.metadata to JSON.
    """
    raise NotImplementedError("TODO: see docs/reference/logging-and-run-metadata.md")
