"""Demonstrate duplicate rejection. NOT a staged failure.

Load the same match twice. The primary key holds, and the log line shows the
split. Closely related to idempotency, and worth showing as a separate beat
because it is the mechanism underneath it.

Three beats on one match from the archive, in an in-memory database:

  1. one batch carrying the match twice   -> one row; the batch is deduplicated
                                             (last row wins) and one is inserted
  2. the same match loaded again          -> still one row; unchanged=1
  3. the match with a corrected gate      -> still one row; updated=1, and the
                                             table holds the corrected figure

Doc: docs/phases/09-break-and-fix.md, "Demonstrate working, do not break"
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

from usl import config  # noqa: E402
from usl.db import row_count  # noqa: E402
from usl.ingest import archive  # noqa: E402
from usl.ingest.footystats import (  # noqa: E402
    ENDPOINT_LEAGUE_MATCHES,
    add_match_id,
    parse_season_matches,
)
from usl.load.raw import upsert_matches  # noqa: E402


def say(text: str) -> None:
    print(f"\n== {text}")


def check(condition: bool, what: str) -> bool:
    print(("   ok   " if condition else "   FAIL ") + what)
    return condition


def stored_attendance(con: duckdb.DuckDBPyConnection, match_id: str) -> str | None:
    row = con.execute(
        "SELECT attendance FROM raw_matches WHERE match_id = ?", [match_id]
    ).fetchone()
    return None if row is None else str(row[0])


def main() -> int:
    """Load one match twice in one batch, then again, then corrected; print the split."""
    say("duplicate rejection")
    print("One archived match, loaded three ways. match_id is the primary key of raw_matches.")

    logging.basicConfig(level=logging.INFO, format="   | %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("usl.ingest.footystats").setLevel(logging.WARNING)

    payload = archive.read_archived(
        ENDPOINT_LEAGUE_MATCHES, {"season_id": config.EXAMPLE_SEASON_ID}
    )
    record = payload["data"][0]
    twice = add_match_id(parse_season_matches({"data": [record, record]}, config.EXAMPLE_SEASON_ID))
    match_id = str(twice["match_id"].iloc[0])
    print(
        f"   {record['home_name']} v {record['away_name']}, match_id {match_id}, "
        f"attendance {record['attendance']}"
    )

    ok = True
    con = duckdb.connect(":memory:")
    try:
        say("1. one batch, the same match twice")
        first = upsert_matches(con, twice)
        print(f"   LoadStats {first}; rows in raw_matches: {row_count(con, 'raw_matches')}")
        ok &= check(row_count(con, "raw_matches") == 1, "one row, not two")
        ok &= check(first.inserted == 1 and first.total == 1, "split: inserted=1")

        say("2. the same match again")
        second = upsert_matches(con, twice.iloc[[0]])
        print(f"   LoadStats {second}; rows in raw_matches: {row_count(con, 'raw_matches')}")
        ok &= check(row_count(con, "raw_matches") == 1, "still one row")
        ok &= check(second.unchanged == 1 and second.inserted == 0, "split: unchanged=1")

        say("3. the same match with a corrected attendance figure")
        corrected_gate = str(int(record["attendance"]) + 1)
        corrected: pd.DataFrame = twice.iloc[[0]].assign(attendance=corrected_gate)
        third = upsert_matches(con, corrected)
        stored = stored_attendance(con, match_id)
        print(f"   LoadStats {third}; rows in raw_matches: {row_count(con, 'raw_matches')}")
        print(f"   attendance stored: {stored} (was {record['attendance']})")
        ok &= check(row_count(con, "raw_matches") == 1, "still one row")
        ok &= check(third.updated == 1 and third.inserted == 0, "split: updated=1")
        ok &= check(stored == corrected_gate, "the corrected figure replaced the old one")
    finally:
        con.close()

    say("result")
    print(
        "the key holds, and the inserted/updated/unchanged line shows exactly what happened"
        if ok
        else "NOT shown - see the FAIL lines above"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
