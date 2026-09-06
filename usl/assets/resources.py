"""The DuckDB resource and the run-context bridge."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
from dagster import AssetCheckExecutionContext, AssetExecutionContext, ConfigurableResource

from usl import config
from usl.db import connect_for_write
from usl.logging_setup import RunContext, current_git_sha, ensure_log_tables, utcnow


class DuckDBResource(ConfigurableResource):
    """The database file, opened through the phase-one lock guard.

    Assets run serially in one process (usl.defs pins the in-process
    executor), so each asset opens and closes its own connection and the
    single-writer rule is never tested by concurrency. A held lock is retried
    and then reported naming the holder, exactly as for the CLI.

    Attributes:
        path: The DuckDB file.
        extract_dir: Where the Tableau extracts go. Defaults to config.EXTRACT_DIR.
        lock_attempts: Override for config.LOCK_MAX_ATTEMPTS (tests).
        lock_backoff: Override for config.LOCK_BACKOFF_BASE_SECONDS (tests).
    """

    path: str = str(config.DB_PATH)
    extract_dir: str | None = None
    lock_attempts: int | None = None
    lock_backoff: float | None = None

    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """A write connection with the log tables present."""
        with connect_for_write(
            Path(self.path), max_attempts=self.lock_attempts, backoff_base=self.lock_backoff
        ) as con:
            ensure_log_tables(con)
            yield con

    @property
    def extract_path(self) -> Path:
        """The extract directory as a Path."""
        return Path(self.extract_dir) if self.extract_dir else config.EXTRACT_DIR


def run_context(context: AssetExecutionContext | AssetCheckExecutionContext) -> RunContext:
    """The phase-one run context for a Dagster run.

    The Dagster run id becomes the run_log run_id, so a row written by an
    asset and the run in the Dagster UI are the same run.

    Args:
        context: The asset or check context.

    Returns:
        A RunContext keyed by the Dagster run id.
    """
    return RunContext(run_id=context.run.run_id, started_at=utcnow(), git_sha=current_git_sha())
