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

import contextlib
import datetime as dt
import json
import logging
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import duckdb

from usl import config

log = logging.getLogger(__name__)

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

STAGES: tuple[str, ...] = ("backfill", "ingest", "transform", "train", "export")

# The metadata keys finish_stage knows how to store. Anything else a stage
# reports is logged at DEBUG and dropped, so a typo cannot fail a run.
_METADATA_COLUMNS: tuple[str, ...] = (
    "rows_read",
    "rows_inserted",
    "rows_updated",
    "rows_unchanged",
    "seasons",
    "max_match_date",
    "null_attendance_pct",
)

_ERROR_MESSAGE_MAX_CHARS = 2000

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def utcnow() -> dt.datetime:
    """Current UTC time as a naive datetime, which is what TIMESTAMP columns hold."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


@dataclass
class RunContext:
    """Metadata for one invocation of the pipeline, shared across its stages.

    A single run_id across ingest, transform, train, and export is what lets you
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

    def __add__(self, other: LoadStats) -> LoadStats:
        return LoadStats(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.unchanged + other.unchanged,
        )


class RedactSecretsFilter(logging.Filter):
    """Scrub the API key out of every record before any handler sees it.

    The key is in every request URL, so one DEBUG line logging a URL would
    write a paid credential into logs/. Belt and braces: the client never logs
    URLs, and this filter makes sure nothing else does either. It replaces the
    configured key wherever it appears, and any 'key=...' query parameter.
    """

    _QUERY_KEY = re.compile(r"(?i)(\bkey=)[^&\s\"']+")

    def __init__(self, secret: str | None = None) -> None:
        super().__init__()
        self.secret = secret if secret is not None else config.FOOTYSTATS_API_KEY

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        cleaned = self._QUERY_KEY.sub(r"\1***", text)
        if self.secret:
            cleaned = cleaned.replace(self.secret, "***")
        if cleaned != text:
            record.msg = cleaned
            record.args = ()
        return True


def configure_logging(level: int = logging.INFO, *, run_id: str | None = None) -> None:
    """Configure the root logger for a run.

    Sets up a console handler and a dated file handler under config.LOG_DIR.
    Call once, at the top of the CLI entry point. Calling it again replaces the
    handlers it installed rather than stacking a second copy.

    Args:
        level: Root log level. INFO is the normal narrative; DEBUG adds
            per-request detail and the SQL being executed.
        run_id: Included in the log filename so a run's file is findable from
            its run_log row.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_usl_handler", False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    redact = RedactSecretsFilter()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(redact)
    console._usl_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y-%m-%d_%H%M%S")
    suffix = f"_{run_id[:8]}" if run_id else ""
    file_handler = logging.FileHandler(config.LOG_DIR / f"{stamp}{suffix}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redact)
    file_handler._usl_handler = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    root.setLevel(level)
    # Third-party chatter drowns the narrative at DEBUG.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def current_git_sha() -> str | None:
    """The short sha of HEAD, or None when git is unavailable or this is not a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=config.PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else None


def new_run_context() -> RunContext:
    """Start a run: generate a run_id, capture the start time and the git sha.

    The git sha matters more than it looks. Six weeks of run history is only
    interpretable if you know which version of the code produced each row.
    Reading it never fails the run - it degrades to None.

    Returns:
        A RunContext to thread through the stages of this invocation.
    """
    return RunContext(run_id=uuid.uuid4().hex, started_at=utcnow(), git_sha=current_git_sha())


def ensure_log_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create run_log and check_log if they do not exist.

    Args:
        con: An open DuckDB connection with write access.
    """
    con.execute(RUN_LOG_DDL)
    con.execute(CHECK_LOG_DDL)


def start_stage(con: duckdb.DuckDBPyConnection, ctx: RunContext, stage: str) -> None:
    """Record that a stage has begun, with status 'running'.

    Writing the row at stage start rather than only at the end is what
    distinguishes "crashed hard" from "never started" - a process killed by the
    OS never gets to write a terminal status, and the leftover 'running' row is
    the evidence.

    Args:
        con: Open DuckDB connection.
        ctx: The run context.
        stage: One of STAGES.
    """
    started = utcnow()
    ctx.stages[stage] = {"started_at": started}
    con.execute(
        """
        INSERT INTO run_log (run_id, stage, started_at, status, git_sha)
        VALUES (?, ?, ?, 'running', ?)
        ON CONFLICT (run_id, stage) DO UPDATE SET
            started_at = excluded.started_at,
            finished_at = NULL,
            status = 'running',
            error_type = NULL,
            error_message = NULL,
            duration_seconds = NULL
        """,
        [ctx.run_id, stage, started, ctx.git_sha],
    )
    log.info("stage %s started (run %s)", stage, ctx.run_id[:8])


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

    Updates the row written by start_stage rather than inserting a second one.
    If start_stage was never called for this stage, a row is inserted so the
    outcome is not lost.

    Args:
        con: Open DuckDB connection.
        ctx: The run context.
        stage: The stage being closed out.
        status: 'success' or 'failed'.
        metadata: Stage metadata - row counts, seasons touched, max match date,
            null attendance percentage. See the field reference doc for the full
            list and the reasoning behind each. Unknown keys are dropped.
        error: The exception, when the stage failed. Its class name and
            (truncated) text are recorded.
    """
    if status not in ("success", "failed"):
        raise ValueError(f"status must be 'success' or 'failed', got {status!r}")
    meta = dict(metadata or {})
    started = ctx.stages.get(stage, {}).get("started_at")
    if started is None:
        start_stage(con, ctx, stage)
        started = ctx.stages[stage]["started_at"]
    # Stamp the finish only once the start is settled: the fallback above stamps
    # its own start time, and a finish taken before it would read as a stage
    # that ended before it began.
    finished = utcnow()
    duration = (finished - started).total_seconds()

    unknown = sorted(set(meta) - set(_METADATA_COLUMNS))
    if unknown:
        log.debug("stage %s reported metadata with no column, dropped: %s", stage, unknown)

    seasons = meta.get("seasons")
    if seasons is not None and not isinstance(seasons, str):
        seasons = json.dumps(sorted(int(s) for s in seasons))
    max_match_date = meta.get("max_match_date")
    if isinstance(max_match_date, dt.datetime):
        max_match_date = max_match_date.date()
    if max_match_date is not None:
        max_match_date = str(max_match_date)

    error_type = type(error).__name__ if error is not None else None
    error_message = str(error)[:_ERROR_MESSAGE_MAX_CHARS] if error is not None else None

    con.execute(
        """
        UPDATE run_log SET
            finished_at         = ?,
            status              = ?,
            rows_read           = ?,
            rows_inserted       = ?,
            rows_updated        = ?,
            rows_unchanged      = ?,
            seasons             = ?,
            max_match_date      = CAST(? AS DATE),
            null_attendance_pct = ?,
            error_type          = ?,
            error_message       = ?,
            duration_seconds    = ?
        WHERE run_id = ? AND stage = ?
        """,
        [
            finished,
            status,
            _int_or_none(meta.get("rows_read")),
            _int_or_none(meta.get("rows_inserted")),
            _int_or_none(meta.get("rows_updated")),
            _int_or_none(meta.get("rows_unchanged")),
            seasons,
            max_match_date,
            _float_or_none(meta.get("null_attendance_pct")),
            error_type,
            error_message,
            duration,
            ctx.run_id,
            stage,
        ],
    )
    ctx.stages[stage].update({"finished_at": finished, "status": status, **meta})
    if status == "success":
        log.info("stage %s finished in %.1fs", stage, duration)
    else:
        log.error("stage %s FAILED after %.1fs: %s: %s", stage, duration, error_type, error_message)


@contextmanager
def stage(con: duckdb.DuckDBPyConnection, ctx: RunContext, name: str) -> Iterator[dict[str, Any]]:
    """Run one stage under the run log: 'running' on entry, a terminal row on exit.

    Yields a dict the stage body fills with metadata (rows_read, seasons, ...).
    An exception is recorded as a failed stage and re-raised - never swallowed.
    That re-raise is the one rule in docs/reference/logging-and-run-metadata.md.

    Args:
        con: Open DuckDB connection.
        ctx: The run context.
        name: Stage name.

    Yields:
        The metadata dict for this stage.
    """
    start_stage(con, ctx, name)
    meta: dict[str, Any] = {}
    try:
        yield meta
    except BaseException as exc:
        _rollback_if_open(con)
        finish_stage(con, ctx, name, status="failed", metadata=meta, error=exc)
        raise
    else:
        finish_stage(con, ctx, name, status="success", metadata=meta)


def log_check_result(con: duckdb.DuckDBPyConnection, ctx: RunContext, result: Any) -> None:
    """Record one check result, passed or failed.

    Logging passes as well as failures is deliberate. A check that has passed for
    six weeks and starts failing is a signal; a check that only writes a row when
    it fails gives you no baseline to notice that against.

    Args:
        con: Open DuckDB connection.
        ctx: The run context.
        result: A CheckResult from usl.transform.checks (duck-typed: name, tier,
            passed, metadata).
    """
    con.execute(
        """
        INSERT INTO check_log (run_id, check_name, tier, passed, metadata, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id, check_name) DO UPDATE SET
            tier = excluded.tier,
            passed = excluded.passed,
            metadata = excluded.metadata,
            checked_at = excluded.checked_at
        """,
        [
            ctx.run_id,
            result.name,
            result.tier,
            bool(result.passed),
            json.dumps(result.metadata, default=str, sort_keys=True),
            utcnow(),
        ],
    )
    log.log(
        logging.INFO if result.passed else logging.ERROR,
        "check %-28s %s  %s",
        result.name,
        "passed" if result.passed else "FAILED",
        json.dumps(result.metadata, default=str, sort_keys=True),
    )


def _rollback_if_open(con: duckdb.DuckDBPyConnection) -> None:
    """Roll back an explicit transaction left open by a failed stage, if any.

    No transaction being open is the normal case, and DuckDB says so with an
    exception, which is suppressed.
    """
    with contextlib.suppress(duckdb.Error):
        con.execute("ROLLBACK")


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)
