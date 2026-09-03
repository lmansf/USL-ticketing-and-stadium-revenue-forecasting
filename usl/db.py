"""DuckDB connection and write strategy.

DuckDB is single-writer. If Tableau Desktop holds the database file open when
Tuesday's scheduled job fires, the write fails - and the failure that matters is
not the crash, it is the silence afterwards while the dashboard shows last week's
numbers to people who believe it.

Handling that is the one exercise in this guide with no worked solution. The
contracts below state what the implementation must satisfy. How it satisfies them
is yours to decide.

See docs/phases/02-duckdb-and-the-lock-problem.md
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb


class DatabaseLockedError(RuntimeError):
    """Raised when the database file could not be opened for writing.

    The message is the deliverable. Six months from now, whoever reads it in a
    log will have forgotten this code exists, and the message is the only thing
    that tells them what to do about it.
    """


def connect_read_only(path: Path) -> duckdb.DuckDBPyConnection:
    """Open the database for reading.

    Multiple readers are fine - the single-writer constraint is about writes.
    Use this from anything that only queries: exports, checks, notebooks.

    Args:
        path: Path to the DuckDB file.

    Returns:
        An open read-only connection.

    Raises:
        FileNotFoundError: If the database does not exist yet. Say so plainly and
            point at the backfill command rather than creating an empty database,
            which would make a missing backfill look like an empty season.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/02-duckdb-and-the-lock-problem.md")


@contextmanager
def connect_for_write(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the database for writing, surviving or reporting a held lock.

    UNGUIDED EXERCISE. There is no solution block for this one in the docs.

    What the implementation must satisfy:

    - Tuesday's scheduled run completes even when Tableau has been left open on
      the database file, or fails in a way that names the cause in one line.
    - A reader that opens the file mid-run sees either the complete previous
      state or the complete new state. Never a half-written one.
    - The failure message, read six months from now by someone who has forgotten
      this code exists, is enough to act on.

    What you have to decide:

    - Retry, or write to a temporary database and swap the file in once it is
      complete, or both. They fail differently and cost differently.
    - If you retry: how many attempts, how long between them, and whether the
      delay is constant or grows.
    - Which exceptions mean "locked" and which mean something genuinely wrong
      that must not be retried. Retrying the wrong error class turns a real bug
      into a slow one.
    - If you swap: what happens if the process dies between writing the temp file
      and replacing the original, and whether the replace operation you chose is
      atomic on the platform this actually runs on.
    - What the run log records in each case, so that demo scenario D1 has a log
      line to point at.

    How you know it works: hold the file open in a second process and run the
    job. Then do it again while killing the job partway through, and check the
    database is still readable and internally consistent afterwards.

    Args:
        path: Path to the DuckDB file.

    Yields:
        An open connection with write access.

    Raises:
        DatabaseLockedError: When the write could not proceed. The message must
            name the likely cause.

    TODO: implement. config.DB_TMP_PATH exists if you take the swap route.
    """
    raise NotImplementedError(
        "UNGUIDED EXERCISE - see docs/phases/02-duckdb-and-the-lock-problem.md, "
        "'Guard two - handle the lock'. This one has no solution block."
    )


def commit_and_swap(tmp_path: Path, final_path: Path) -> None:
    """Make a completed temporary database the live one.

    Only needed if you took the write-to-temp-then-swap route in
    connect_for_write. Part of the same unguided exercise.

    What it must satisfy:

    - A concurrent reader sees the old file or the new file, never a partial one.
    - It is correct on the platform this actually runs on, which per
      docs/mvp/05-mvp-schedule.md is Windows.
    - A crash partway through leaves a working database, not a missing one.

    Args:
        tmp_path: The completed temporary database. Must already be closed.
        final_path: The live database path to replace.

    TODO: implement, or delete if you took the retry route instead. Deleting it
    is a legitimate answer - say in your notes which route you chose and why.
    """
    raise NotImplementedError(
        "UNGUIDED EXERCISE - see docs/phases/02-duckdb-and-the-lock-problem.md"
    )


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Whether a table exists in the connected database.

    Args:
        con: Open connection.
        name: Table name, unqualified.

    Returns:
        True if the table exists.

    TODO: implement against duckdb_tables() or information_schema.
    """
    raise NotImplementedError("TODO")


def row_count(con: duckdb.DuckDBPyConnection, name: str) -> int:
    """Row count for a table.

    Used all over the checks and the run log. Row counts are the second,
    independent signal that catches silent data loss when a null check does not -
    see docs/phases/03-club-name-consistency.md.

    Args:
        con: Open connection.
        name: Table name, unqualified.

    Returns:
        Number of rows.

    TODO: implement.
    """
    raise NotImplementedError("TODO")
