"""D1 - The locked DuckDB file.

Open the database in a second process, trigger the run, watch it fail on the
lock with a message that names the cause. Close the holder, re-run, green.

What it shows: the failure is legible, not mysterious. The important half is not
that it failed - it is that six months from now the person reading that log line
knows immediately what to do.

The strategy under test is the one chosen in usl/db.py::connect_for_write:
RETRY with backoff, then fail naming the holder. Nothing is written and the run
log records nothing, because the run log lives inside the locked file; the
evidence is the log line, the exit code (3), and logs/. The other strategy,
write-to-temp-then-swap, would fail on the same file for the same reason: DuckDB
refuses a read-only open while a writer holds the lock, and Windows refuses to
replace an open file, so a swap adds nothing the holder letting go does not.

Runs against a scratch copy of the database, never the real one, and uses the
CLI's hidden --lock-attempts / --lock-backoff overrides so the demo takes
seconds rather than the thirty-second production retry window.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from usl import config  # noqa: E402

# Runs in a second process: takes the write lock and keeps it until killed.
HOLDER = """
import sys, time
import duckdb
con = duckdb.connect(sys.argv[1])
print("holding", flush=True)
while True:
    time.sleep(0.2)
"""

EXIT_LOCKED = 3


def say(text: str) -> None:
    print(f"\n== {text}")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run python -m usl.run with the given arguments, capturing both streams."""
    command = [sys.executable, "-m", "usl.run", *args]
    print("$ " + " ".join(command[1:]))
    return subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def show(lines: list[str]) -> None:
    for line in lines:
        print("   | " + line)


def scratch_database(directory: Path) -> Path:
    """A private copy of the database to lock, so the real one is never touched."""
    scratch = directory / "usl_demo.duckdb"
    if config.DB_PATH.exists():
        shutil.copyfile(config.DB_PATH, scratch)
        print(f"copied {config.DB_PATH} to {scratch}")
    else:
        print(f"{config.DB_PATH} does not exist yet - building the scratch copy from the archive")
        result = run_cli("backfill", "--db", str(scratch))
        if result.returncode != 0:
            print(result.stderr)
            raise SystemExit("could not build the scratch database")
    return scratch


def run_log_rows(path: Path) -> int:
    con = duckdb.connect(str(path), read_only=True)
    try:
        row = con.execute("SELECT count(*) FROM run_log").fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def main() -> int:
    """Hold the database open, run the transform, show the failure, release, re-run."""
    say("D1 - the locked file")
    print("A second process will hold the write lock on a scratch copy of the database.")
    print("The transform runs against it, fails after a short retry, names the holder,")
    print(f"and exits {EXIT_LOCKED}. Then the holder lets go and the same command goes green.")

    ok = True
    holder: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="usl-d1-") as tmp:
        try:
            scratch = scratch_database(Path(tmp))
            rows_before = run_log_rows(scratch)

            say("start the holder")
            holder = subprocess.Popen(
                [sys.executable, "-c", HOLDER, str(scratch)],
                stdout=subprocess.PIPE,
                text=True,
            )
            assert holder.stdout is not None
            ready = holder.stdout.readline().strip()
            if ready != "holding":
                raise SystemExit(f"holder did not take the lock (said {ready!r})")
            print(
                f"PID {holder.pid} holds {scratch.name} open for writing (think: Tableau Desktop)"
            )

            say("run the transform against the locked file (2 attempts, 1s apart)")
            failed = run_cli(
                "transform",
                "--db",
                str(scratch),
                "--lock-attempts",
                "2",
                "--lock-backoff",
                "1",
            )
            print(f"exit code {failed.returncode}")
            lock_lines = [line for line in failed.stderr.splitlines() if "lock" in line.lower()]
            show(lock_lines)

            if failed.returncode != EXIT_LOCKED:
                ok = False
                print(f"FAIL: expected exit code {EXIT_LOCKED}")
                print(failed.stderr)
            if "is locked by another process" not in failed.stderr:
                ok = False
                print("FAIL: the DatabaseLockedError line is missing")
            if f"(PID {holder.pid})" not in failed.stderr:
                ok = False
                print(f"FAIL: the message does not name the holder's PID {holder.pid}")

            say("why a temp-file swap would not have helped")
            try:
                run_log_rows(scratch)
                ok = False
                print("FAIL: a read-only open succeeded while the writer held the file")
            except duckdb.IOException as exc:
                print(f"   even a read-only open is refused: {str(exc).splitlines()[0][:110]}")
                print(
                    "   DuckDB refuses the file to every other process while a writer holds it, "
                    "so swapping a fresh file in would fail on the same lock. Only the holder "
                    "letting go helps, which is what the retry waits for."
                )

            say("close the holder")
            holder.terminate()
            holder.wait(timeout=10)
            holder = None
            print("holder released the lock")
            if run_log_rows(scratch) != rows_before:
                ok = False
                print("FAIL: run_log changed, but nothing should have been written")
            else:
                print(
                    f"run_log still has {rows_before} row(s): nothing was written, as the message "
                    "says - the run log lives inside the locked file, so the evidence is the "
                    "ERROR line above, exit code 3, and the file under logs/."
                )

            say("re-run")
            green = run_cli("transform", "--db", str(scratch))
            print(f"exit code {green.returncode}")
            show(
                [
                    line
                    for line in green.stderr.splitlines()
                    if "stage transform" in line or "check " in line
                ]
            )
            if green.returncode != 0:
                ok = False
                print("FAIL: expected exit code 0 after the holder released the lock")
                print(green.stderr)
            elif run_log_rows(scratch) != rows_before + 1:
                ok = False
                print("FAIL: the green run should have added exactly one run_log row")
        finally:
            if holder is not None and holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)

    say("result")
    print(
        "D1 shown: legible lock failure, exit code 3, green on re-run"
        if ok
        else "D1 NOT shown - see the FAIL lines above"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
