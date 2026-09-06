"""Demonstrate idempotency. NOT a staged failure.

Run Tuesday's job twice. The second run reports zero inserted, N unchanged, and
attendance totals unchanged. This works out of the box by design.

Frame it as: "re-running is safe, and here is the log line that proves it."

The load runs against a fresh temporary database from the committed archive, so
it needs no key and touches nothing in data/.

Doc: docs/phases/09-break-and-fix.md, "Demonstrate working, do not break"
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from usl import config  # noqa: E402
from usl.load.raw import backfill  # noqa: E402


def say(text: str) -> None:
    print(f"\n== {text}")


def check(condition: bool, what: str) -> bool:
    print(("   ok   " if condition else "   FAIL ") + what)
    return condition


def totals(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Row count and attendance sum of raw_matches."""
    row = con.execute(
        "SELECT count(*), sum(TRY_CAST(attendance AS BIGINT)) FROM raw_matches"
    ).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def main() -> int:
    """Run the load twice, print both LoadStats splits and the attendance totals."""
    say("idempotency")
    print(f"Load season_id {config.EXAMPLE_SEASON_ID} from the archive into a fresh database,")
    print("twice. The second load must insert nothing and change no attendance figure.")

    logging.basicConfig(level=logging.INFO, format="   | %(levelname)-7s %(name)s: %(message)s")
    for name in ("usl.ingest.footystats",):
        logging.getLogger(name).setLevel(logging.WARNING)

    ok = True
    with tempfile.TemporaryDirectory(prefix="usl-idempotency-") as tmp:
        con = duckdb.connect(str(Path(tmp) / "fresh.duckdb"))
        try:
            say("first load")
            first = backfill(con, [config.EXAMPLE_SEASON_ID])
            rows1, attendance1 = totals(con)
            print(f"   LoadStats {first}")
            print(f"   raw_matches rows={rows1} attendance total={attendance1:,}")

            say("second load, same archive")
            second = backfill(con, [config.EXAMPLE_SEASON_ID])
            rows2, attendance2 = totals(con)
            print(f"   LoadStats {second}")
            print(f"   raw_matches rows={rows2} attendance total={attendance2:,}")

            say("compare")
            ok &= check(first.inserted == 380 and first.updated == 0, "first load: 380 inserted")
            ok &= check(second.inserted == 0, "second load: 0 inserted")
            ok &= check(second.unchanged == first.inserted, "second load: every row unchanged")
            ok &= check(rows1 == rows2 == 380, "row count still 380")
            ok &= check(attendance1 == attendance2, "attendance total identical")
        finally:
            con.close()

    say("result")
    print(
        "re-running is safe, and the inserted/updated/unchanged line is the proof"
        if ok
        else "NOT shown - see the FAIL lines above"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
