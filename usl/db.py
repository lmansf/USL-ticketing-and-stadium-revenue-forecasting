"""DuckDB connection and write strategy.

DuckDB is single-writer. If Tableau Desktop holds the database file open when
Tuesday's scheduled job fires, the write fails - and the failure that matters is
not the crash, it is the silence afterwards while the dashboard shows last week's
numbers to people who believe it.

Handling that is the one exercise in the guide with no worked solution. The
route taken here is RETRY, not write-to-temp-then-swap. The reasoning, in
short (the long version is docs/reference/build-decisions.md):

  - DuckDB is already transactional. A committed CREATE OR REPLACE TABLE is
    atomic and a crash mid-write is rolled back from the write-ahead log on the
    next open, so "a reader sees the old state or the new state, never a
    half-written one" is a property of the engine, not something a temp-file
    swap has to add.
  - A swap does not solve the actual problem. If Tableau holds the file open,
    Windows refuses to replace it (and DuckDB refuses to open it read-only
    while a writer holds it anyway). The only thing that helps is the holder
    letting go, so the honest strategy is: wait a bounded time for that, then
    fail with a message that says exactly who is holding it.
  - Only lock errors are retried. A corrupt file or a missing directory is
    raised on the first attempt; retrying it would turn a real bug into a slow
    one.

See docs/phases/02-duckdb-and-the-lock-problem.md
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from usl import config

log = logging.getLogger(__name__)

# DuckDB's message when another process holds the file:
#   IO Error: Could not set lock on file "<path>": Conflicting lock is held in
#   <executable> (PID <n>). See also https://duckdb.org/docs/stable/connect/concurrency
_LOCK_HOLDER = re.compile(r"Conflicting lock is held in (?P<proc>.+?) \(PID (?P<pid>\d+)\)")


class DatabaseLockedError(RuntimeError):
    """Raised when the database file could not be opened for writing.

    The message is the deliverable. Six months from now, whoever reads it in a
    log will have forgotten this code exists, and the message is the only thing
    that tells them what to do about it.
    """


def is_lock_error(exc: BaseException) -> bool:
    """Whether an exception means 'another process holds the database file'.

    Only this class of error is worth retrying. DuckDB raises IOException for
    a missing directory and a corrupt file too, and neither of those improves
    with waiting.

    Args:
        exc: The exception raised by duckdb.connect.

    Returns:
        True for a held-lock error.
    """
    return isinstance(exc, duckdb.IOException) and "lock" in str(exc).lower()


def describe_lock_holder(exc: BaseException) -> str:
    """One readable clause naming who holds the lock, from DuckDB's message.

    Args:
        exc: A lock error.

    Returns:
        e.g. 'held by /usr/bin/python3.11 (PID 2384)', or DuckDB's own text
        when the message does not carry a holder.
    """
    match = _LOCK_HOLDER.search(str(exc))
    if match:
        return f"held by {match.group('proc')} (PID {match.group('pid')})"
    return str(exc).strip()


def connect_read_only(path: Path) -> duckdb.DuckDBPyConnection:
    """Open the database for reading.

    Multiple readers are fine - the single-writer constraint is about writes.
    Use this from anything that only queries: exports, checks, notebooks.

    Args:
        path: Path to the DuckDB file.

    Returns:
        An open read-only connection.

    Raises:
        FileNotFoundError: If the database does not exist yet. Says so plainly
            and points at the backfill command rather than creating an empty
            database, which would make a missing backfill look like an empty
            season.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Nothing has been loaded yet - run "
            "'python -m usl.run backfill' (served from data/raw_archive/, no key "
            "needed) before anything that reads the database."
        )
    return duckdb.connect(str(path), read_only=True)


@contextmanager
def connect_for_write(
    path: Path,
    *,
    max_attempts: int | None = None,
    backoff_base: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the database for writing, surviving or reporting a held lock.

    Strategy: retry with exponential backoff, lock errors only.

    - Attempt to open. A held lock is retried up to config.LOCK_MAX_ATTEMPTS
      times with delays of base, 2*base, 4*base ... (about 30 seconds in total
      at the defaults). That covers a Tableau extract refresh or a DuckDB CLI
      someone is about to close; it does not cover a workbook left open all
      night, and nothing can.
    - Any other IOException - missing directory, corrupt file - is raised on
      the first attempt, unchanged.
    - When the attempts run out, DatabaseLockedError names the holder (DuckDB
      reports the executable and PID), says what to do, and says that nothing
      was written.

    What the run log records: nothing, and that is worth being clear about.
    The run log is a table inside the locked database, so a lock failure
    cannot be written there. It goes to the file log under logs/ and to the
    process exit code, which is why scripts/run_weekly.* propagate that code
    to the scheduler. See docs/reference/build-decisions.md.

    Args:
        path: Path to the DuckDB file. Parent directories are created.
        max_attempts: Override config.LOCK_MAX_ATTEMPTS (tests use 1 or 2).
        backoff_base: Override config.LOCK_BACKOFF_BASE_SECONDS.
        sleep: Injectable for tests, so a retry test does not actually wait.

    Yields:
        An open connection with write access. Closed on exit.

    Raises:
        DatabaseLockedError: When the lock was still held after every attempt.
        duckdb.IOException: For any non-lock failure to open the file, on the
            first attempt and without a retry.
        OSError: When the parent directory cannot be created, for example
            because a regular file already sits where the directory should be.
    """
    path = Path(path)
    attempts = max_attempts if max_attempts is not None else config.LOCK_MAX_ATTEMPTS
    base = backoff_base if backoff_base is not None else config.LOCK_BACKOFF_BASE_SECONDS
    if attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    path.parent.mkdir(parents=True, exist_ok=True)

    con: duckdb.DuckDBPyConnection | None = None
    waited = 0.0
    for attempt in range(1, attempts + 1):
        try:
            con = duckdb.connect(str(path))
            break
        except duckdb.IOException as exc:
            if not is_lock_error(exc):
                raise
            holder = describe_lock_holder(exc)
            if attempt == attempts:
                raise DatabaseLockedError(
                    f"{path} is locked by another process - {holder}. DuckDB allows one "
                    "writer at a time, so the usual cause is Tableau Desktop or a DuckDB CLI "
                    f"left open on the file. Close it and re-run. Gave up after {attempts} "
                    f"attempt(s) over {waited:.0f}s. Nothing was written, and the run log "
                    "could not be updated because it lives in this database."
                ) from exc
            delay = base * (2 ** (attempt - 1))
            log.warning(
                "database %s is locked (%s) - attempt %d/%d, retrying in %.0fs",
                path.name,
                holder,
                attempt,
                attempts,
                delay,
            )
            sleep(delay)
            waited += delay

    assert con is not None  # the loop either broke with a connection or raised
    try:
        yield con
    finally:
        con.close()


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Whether a table exists in the connected database.

    Args:
        con: Open connection.
        name: Table name, unqualified.

    Returns:
        True if the table exists.
    """
    row = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ? AND NOT internal",
        [name],
    ).fetchone()
    return bool(row and row[0])


def row_count(con: duckdb.DuckDBPyConnection, name: str) -> int:
    """Row count for a table.

    Used all over the checks and the run log. Row counts are the second,
    independent signal that catches silent data loss when a null check does not -
    see docs/phases/03-club-name-consistency.md.

    Args:
        con: Open connection.
        name: Table name, unqualified. Must be a plain identifier.

    Returns:
        Number of rows.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"not a plain table name: {name!r}")
    row = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()
    return int(row[0]) if row else 0
