"""DuckDB write strategy under a held lock.

usl/db.py::connect_for_write was the one unguided exercise in the guide. The
route taken is retry with exponential backoff, lock errors only, then a
DatabaseLockedError that names the holder. These tests cover the five
scenarios the exercise lists, from the writer's side:

  1. A second process holds the file. Bounded retries, then a message that
     names the holding PID, says what to do, and says nothing was written.
  2. The writer is killed partway through a write. The file reopens and holds
     exactly the committed state; the uncommitted transaction is gone.
  3. A reader sees the complete old state or the complete new state. DuckDB
     gives that at the statement level and through snapshot isolation.
  4. A non-lock error - corrupt file, unusable directory - is raised on the
     first attempt. Retrying it would turn a real bug into a slow one.
  5. The run log records what it can, which is nothing while the database is
     locked, and the error says so; after the holder lets go a normal round
     trip records success.

The holder is always a real second process. A second connection in the same
process shares DuckDB's database handle and never contends for the file lock,
so it cannot stand in for Tableau. Every holder is terminated in a finally.

Doc: docs/phases/02-duckdb-and-the-lock-problem.md, "Guard two - handle the lock"
     docs/reference/build-decisions.md, "Phase 02 - the lock"
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path

import duckdb
import pytest

from usl.db import (
    DatabaseLockedError,
    connect_for_write,
    connect_read_only,
    describe_lock_holder,
    is_lock_error,
    row_count,
    table_exists,
)
from usl.logging_setup import ensure_log_tables, new_run_context, stage

# Opens the file for writing and holds it until killed. argv[1] is the path.
_HOLDER_SCRIPT = """
import sys, time
import duckdb
con = duckdb.connect(sys.argv[1])
print("held", flush=True)
time.sleep(120)
"""

# Commits a small table, then stages a much larger insert inside an explicit
# transaction and waits to be killed before committing it. argv[1] is the
# path, argv[2] the committed row count, argv[3] the uncommitted row count.
_KILLED_WRITER_SCRIPT = """
import sys, time
import duckdb
path, committed, staged = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
con = duckdb.connect(path)
con.execute(f"CREATE TABLE t AS SELECT range AS i FROM range({committed})")
con.execute("BEGIN")
con.execute(f"INSERT INTO t SELECT range FROM range({committed}, {committed + staged})")
print("uncommitted", flush=True)
time.sleep(120)
"""


def _read_marker(proc: subprocess.Popen[str], marker: str, timeout: float = 30.0) -> None:
    """Block until the subprocess prints its marker line, or fail with its stderr."""
    stream = proc.stdout
    assert stream is not None
    got: list[str] = []
    reader = threading.Thread(target=lambda: got.append(stream.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    line = got[0].strip() if got else "<nothing within timeout>"
    if line != marker:
        proc.kill()
        _, err = proc.communicate(timeout=10)
        pytest.fail(f"holder did not print {marker!r}, got {line!r}. stderr:\n{err}")


def _wait_for_lock(path: Path, timeout: float = 10.0) -> None:
    """Poll until a read-only open is refused with DuckDB's held-lock error.

    Deliberately does not use is_lock_error: the precondition of these tests
    should not depend on the code they test.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            duckdb.connect(str(path), read_only=True).close()
        except duckdb.IOException as exc:
            if "Conflicting lock" in str(exc):
                return
        time.sleep(0.02)
    pytest.fail(f"no other process took the lock on {path} within {timeout}s")


def _stop(proc: subprocess.Popen[str]) -> None:
    """Kill the holder if it is still running and reap it."""
    if proc.poll() is None:
        proc.kill()
    # Only a wedged child that never closes its pipes can make this time out.
    with suppress(subprocess.TimeoutExpired):
        proc.communicate(timeout=10)


@contextmanager
def _holding(
    path: Path, script: str = _HOLDER_SCRIPT, *args: str, marker: str = "held"
) -> Iterator[subprocess.Popen[str]]:
    """Run a second process that holds the lock on path, and always stop it.

    Yields once the holder has printed its marker AND this process has seen
    the lock refused - the marker alone is not enough because this process
    must not touch the file before the holder has it (an early read-only
    open here would take a shared lock and block the holder instead).
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _read_marker(proc, marker)
        _wait_for_lock(path)
        yield proc
    finally:
        _stop(proc)


# ---------------------------------------------------------------------------
# 1. A second process holds the file
# ---------------------------------------------------------------------------


def test_held_lock_is_retried_then_reported_with_the_holder(tmp_path: Path) -> None:
    """Bounded retries, then a message somebody can act on six months from now.

    The message is the deliverable: it names the file, the holding PID, what
    to do, and that nothing was written. With two attempts exactly one
    backoff sleep happens, and it goes through the injected sleep so the test
    does not wait.
    """
    path = tmp_path / "t.duckdb"
    sleeps: list[float] = []

    with _holding(path) as holder:
        with (
            pytest.raises(DatabaseLockedError) as excinfo,
            connect_for_write(path, max_attempts=2, backoff_base=0.01, sleep=sleeps.append),
        ):
            pytest.fail("the connection must not open while another process holds the file")

        message = str(excinfo.value)
        assert str(path) in message
        assert "held by" in message and f"(PID {holder.pid})" in message
        assert "Close it and re-run" in message
        assert "Gave up after 2 attempt(s)" in message
        assert "Nothing was written" in message
        assert sleeps == [0.01]
        # The original DuckDB error is chained, not lost.
        assert isinstance(excinfo.value.__cause__, duckdb.IOException)

    # Now that the holder is gone: "nothing was written" was true, not just said.
    with connect_for_write(path, max_attempts=1, sleep=sleeps.append) as con:
        row = con.execute("SELECT count(*) FROM duckdb_tables() WHERE NOT internal").fetchone()
        assert row is not None and row[0] == 0
    assert sleeps == [0.01]


# ---------------------------------------------------------------------------
# 2. The writer is killed partway through a write
# ---------------------------------------------------------------------------


def test_writer_killed_mid_transaction_leaves_the_committed_state(tmp_path: Path) -> None:
    """SIGKILL between a commit and the next one: the file holds exactly the commit.

    The writer commits a small table, stages a much larger insert in an open
    transaction, and is killed with no chance to close, roll back, or
    checkpoint. The write-ahead log it leaves behind is replayed on the next
    open, and the uncommitted rows were never in it.
    """
    path = tmp_path / "t.duckdb"
    committed, staged = 100, 50_000

    with _holding(
        path, _KILLED_WRITER_SCRIPT, str(committed), str(staged), marker="uncommitted"
    ) as writer:
        writer.kill()
        writer.wait(timeout=10)
        assert writer.returncode != 0

    # The recovery path is really exercised: the crash left a WAL behind.
    assert path.with_name(path.name + ".wal").exists()

    with closing(duckdb.connect(str(path))) as con:
        assert row_count(con, "t") == committed

    # And the pipeline's own opener works on the recovered file.
    sleeps: list[float] = []
    with connect_for_write(path, max_attempts=1, sleep=sleeps.append) as con:
        assert row_count(con, "t") == committed
        con.execute("INSERT INTO t VALUES (-1)")
    assert sleeps == []
    with closing(connect_read_only(path)) as ro:
        assert row_count(ro, "t") == committed + 1


# ---------------------------------------------------------------------------
# 3. A reader sees the complete old state or the complete new state
# ---------------------------------------------------------------------------


def test_reader_sees_a_whole_state_never_a_partial_one(tmp_path: Path) -> None:
    """Atomic statements, atomic rollback, and snapshot isolation on a second handle.

    Honest about what this can and cannot show. A cross-process reader is
    refused the file while a writer holds it - that is the lock scenario in
    test 1, tested from the writer's side - so "mid-run" here is a second
    connection in the same process, which shares DuckDB's database handle and
    reads through its own transaction. What the engine guarantees, and what
    is asserted:

      - after CREATE OR REPLACE TABLE commits, the count is M, whole;
      - a rewrite inside BEGIN ... ROLLBACK (what stage() does when the body
        raises) leaves the count at N, whole;
      - the second handle never sees the uncommitted M.
    """
    path = tmp_path / "t.duckdb"
    n, m = 5, 9

    with connect_for_write(path, max_attempts=1) as con:
        con.execute(f"CREATE TABLE t AS SELECT range AS i FROM range({n})")

    with (
        connect_for_write(path, max_attempts=1) as writer,
        closing(duckdb.connect(str(path))) as reader,
    ):
        assert row_count(reader, "t") == n

        # A rewrite that does not reach COMMIT leaves the old state intact.
        writer.execute("BEGIN")
        writer.execute(f"CREATE OR REPLACE TABLE t AS SELECT range AS i FROM range({m})")
        assert row_count(writer, "t") == m
        assert row_count(reader, "t") == n
        writer.execute("ROLLBACK")
        assert row_count(writer, "t") == n
        assert row_count(reader, "t") == n

        # A committed rewrite is visible whole.
        writer.execute(f"CREATE OR REPLACE TABLE t AS SELECT range AS i FROM range({m})")
        assert row_count(writer, "t") == m
        assert row_count(reader, "t") == m

    with closing(connect_read_only(path)) as ro:
        assert row_count(ro, "t") == m


# ---------------------------------------------------------------------------
# 4. A non-lock error is not retried
# ---------------------------------------------------------------------------


def test_corrupt_file_is_raised_on_the_first_attempt(tmp_path: Path) -> None:
    """A file that is not a database does not improve with waiting."""
    path = tmp_path / "corrupt.duckdb"
    path.write_text("this is not a duckdb file\n" * 40)
    sleeps: list[float] = []

    with (
        pytest.raises(duckdb.IOException) as excinfo,
        connect_for_write(path, max_attempts=5, backoff_base=0.01, sleep=sleeps.append),
    ):
        pytest.fail("a corrupt file must not open")

    assert not isinstance(excinfo.value, DatabaseLockedError)
    assert not is_lock_error(excinfo.value)
    assert sleeps == []


def test_unusable_parent_directory_is_raised_on_the_first_attempt(tmp_path: Path) -> None:
    """A path whose parent is a regular file cannot be created, and is not retried.

    This fails in the directory creation before DuckDB is reached, so the
    error is the OS's rather than DuckDB's. Either way: first attempt, no
    sleep, not a lock.
    """
    blocker = tmp_path / "file.txt"
    blocker.write_text("in the way")
    path = blocker / "db.duckdb"
    sleeps: list[float] = []

    with (
        pytest.raises((duckdb.IOException, OSError)) as excinfo,
        connect_for_write(path, max_attempts=5, backoff_base=0.01, sleep=sleeps.append),
    ):
        pytest.fail("a database under a regular file must not open")

    assert not isinstance(excinfo.value, DatabaseLockedError)
    assert sleeps == []


def test_lock_errors_are_classified_by_type_and_message() -> None:
    """Which exceptions mean 'locked' - and only those are retried."""
    text = (
        'IO Error: Could not set lock on file "/data/usl.duckdb": Conflicting lock is held in '
        "/usr/bin/python3.11 (PID 42). See also https://duckdb.org/docs/stable/connect/concurrency"
    )
    locked = duckdb.IOException(text)
    assert is_lock_error(locked)
    assert describe_lock_holder(locked) == "held by /usr/bin/python3.11 (PID 42)"

    # Right class, wrong message: a missing directory or a corrupt file.
    assert not is_lock_error(duckdb.IOException('IO Error: Cannot open file "x": Not a directory'))
    assert not is_lock_error(duckdb.IOException("IO Error: exists, but it is not a valid database"))
    # Right word, wrong class: never retried on the strength of a substring.
    assert not is_lock_error(RuntimeError("could not acquire lock"))
    assert not is_lock_error(duckdb.CatalogException("no table named lock"))

    # A lock message that does not carry a holder falls back to DuckDB's own text.
    bare = duckdb.IOException("  IO Error: Could not set lock on file  ")
    assert is_lock_error(bare)
    assert describe_lock_holder(bare) == "IO Error: Could not set lock on file"


def test_zero_attempts_is_rejected(tmp_path: Path) -> None:
    """max_attempts=0 would silently yield nothing; say so instead."""
    with (
        pytest.raises(ValueError, match="at least 1"),
        connect_for_write(tmp_path / "t.duckdb", max_attempts=0),
    ):
        pytest.fail("must not open with zero attempts")


# ---------------------------------------------------------------------------
# 5. The run log records what it can
# ---------------------------------------------------------------------------


def test_locked_database_cannot_record_the_failure_and_says_so(tmp_path: Path) -> None:
    """The run log lives inside the locked file, so the error has to carry the story.

    Demo scenario D1 points at the file log line and the exit code, not at a
    run_log row. After the holder lets go, a full stage() round trip records
    status success in the same file.
    """
    path = tmp_path / "t.duckdb"
    sleeps: list[float] = []

    with _holding(path):
        with (
            pytest.raises(DatabaseLockedError) as excinfo,
            connect_for_write(path, max_attempts=1, sleep=sleeps.append),
        ):
            pytest.fail("the connection must not open while another process holds the file")
        message = str(excinfo.value)
        assert "run log" in message
        assert "could not be updated" in message
        assert "lives in this database" in message
        assert sleeps == []

    ctx = new_run_context()
    with connect_for_write(path, max_attempts=1, sleep=sleeps.append) as con:
        ensure_log_tables(con)
        with stage(con, ctx, "transform") as meta:
            con.execute("CREATE TABLE t AS SELECT 1 AS x")
            meta["rows_read"] = row_count(con, "t")

    with closing(connect_read_only(path)) as ro:
        rows = ro.execute(
            "SELECT stage, status, rows_read, error_type FROM run_log WHERE run_id = ?",
            [ctx.run_id],
        ).fetchall()
        assert rows == [("transform", "success", 1, None)]
        assert row_count(ro, "t") == 1


def test_retry_succeeds_once_the_holder_lets_go(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The case the retry exists for: a holder that is about to close.

    Real sleeps here, on purpose. The holder is killed about a second in;
    with a half-second base the first two attempts fail and a later one
    opens the file. Each failed attempt is a WARNING that says 'retrying' and
    names the holder, which is the log line to read when Tuesday was slow.
    """
    path = tmp_path / "t.duckdb"
    caplog.set_level(logging.WARNING, logger="usl.db")

    with _holding(path) as holder:
        release = threading.Timer(1.0, holder.kill)
        release.start()
        try:
            started = time.monotonic()
            with connect_for_write(path, max_attempts=5, backoff_base=0.5) as con:
                elapsed = time.monotonic() - started
                con.execute("CREATE TABLE t AS SELECT 1 AS x")
                assert row_count(con, "t") == 1
        finally:
            release.cancel()

    # At least one real backoff happened before the file opened.
    assert elapsed >= 0.5

    retries = [r for r in caplog.records if r.name == "usl.db" and r.levelno == logging.WARNING]
    assert retries, "a retried lock must leave a WARNING in the log"
    assert all("retrying" in r.getMessage() for r in retries)
    first = retries[0].getMessage()
    assert "attempt 1/5" in first
    assert f"(PID {holder.pid})" in first
    assert path.name in first

    with closing(connect_read_only(path)) as ro:
        assert row_count(ro, "t") == 1


def test_read_only_open_of_a_missing_database_names_the_backfill(tmp_path: Path) -> None:
    """A missing file must not become an empty database that looks like an empty season."""
    path = tmp_path / "missing.duckdb"
    with pytest.raises(FileNotFoundError, match="backfill"):
        connect_read_only(path)
    assert not path.exists()


def test_table_exists_and_row_count(con: duckdb.DuckDBPyConnection) -> None:
    """The two helpers every check and the run log lean on."""
    assert not table_exists(con, "t")
    con.execute("CREATE TABLE t AS SELECT range AS i FROM range(7)")
    assert table_exists(con, "t")
    assert row_count(con, "t") == 7

    con.execute("CREATE VIEW v AS SELECT * FROM t")
    assert not table_exists(con, "v")
    assert not table_exists(con, "T")

    con.execute("CREATE TABLE empty (x INTEGER)")
    assert row_count(con, "empty") == 0

    with pytest.raises(duckdb.CatalogException):
        row_count(con, "no_such_table")


@pytest.mark.parametrize("name", ["t; DROP TABLE t", 'run_log"', "1abc", "", "a b", "run-log"])
def test_row_count_rejects_a_name_that_is_not_an_identifier(
    con: duckdb.DuckDBPyConnection, name: str
) -> None:
    """The name is interpolated into SQL, so anything but a plain identifier is refused."""
    con.execute("CREATE TABLE t AS SELECT 1 AS x")
    with pytest.raises(ValueError, match="not a plain table name"):
        row_count(con, name)
    assert table_exists(con, "t")
