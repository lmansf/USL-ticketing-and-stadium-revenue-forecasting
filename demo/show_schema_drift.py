"""Demonstrate schema drift detection. NOT a staged failure.

Feed an archived response with a required field removed. The parser raises,
naming expected versus found. Correct behaviour on display, not a bug being
patched.

This matters more with an API than it did with scraped HTML, and for a specific
reason: the match-detail endpoint is undocumented. It carries no versioning
promise, no deprecation notice, and no guarantee its field set is the same for
USL as for the leagues it was presumably built against. A guard that fails loudly
is the only warning you will get.

The fixture is the real archived payload cut to five records with homeID and
awayID deleted from each. It lives in demo/fixtures/ rather than
data/raw_archive/, because the archive is meant to be a faithful record of what
the API actually said.

Doc: docs/phases/09-break-and-fix.md, "Demonstrate working, do not break"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from usl import config  # noqa: E402
from usl.ingest import archive  # noqa: E402
from usl.ingest.footystats import (  # noqa: E402
    ENDPOINT_LEAGUE_MATCHES,
    REQUIRED_MATCH_FIELDS,
    SchemaDriftError,
    parse_season_matches,
)

FIXTURE = config.FIXTURE_DIR / f"league-matches_season_id_{config.EXAMPLE_SEASON_ID}_drifted.json"
REMOVED = ("homeID", "awayID")


def say(text: str) -> None:
    print(f"\n== {text}")


def check(condition: bool, what: str) -> bool:
    print(("   ok   " if condition else "   FAIL ") + what)
    return condition


def main() -> int:
    """Parse the faithful archive, then the drifted fixture, and print the error."""
    say("schema drift")
    print("The parser validates by field name. Required fields:")
    print(f"   {sorted(REQUIRED_MATCH_FIELDS)}")

    ok = True

    say("the faithful archive parses")
    payload = archive.read_archived(
        ENDPOINT_LEAGUE_MATCHES, {"season_id": config.EXAMPLE_SEASON_ID}
    )
    frame = parse_season_matches(payload, config.EXAMPLE_SEASON_ID)
    ok &= check(len(frame) == 380, f"{len(frame)} rows, {len(payload['data'][0])} fields each")

    say(f"the drifted fixture: {FIXTURE.relative_to(REPO_ROOT)}")
    drifted = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fields = set(drifted["data"][0])
    print(
        f"   {len(drifted['data'])} records, {len(fields)} fields each, "
        f"{' and '.join(REMOVED)} removed"
    )
    try:
        parse_season_matches(drifted, config.EXAMPLE_SEASON_ID)
        ok &= check(False, "SchemaDriftError was raised")
    except SchemaDriftError as exc:
        message = str(exc)
        print(f"   SchemaDriftError: {message[:160]}...")
        ok &= check(True, "the parser refused the payload")
        ok &= check(all(name in message for name in REMOVED), "names the missing fields")
        ok &= check("found" in message, "names the fields it found")
        ok &= check(f"season_id {config.EXAMPLE_SEASON_ID}" in message, "names the season")

    say("result")
    print(
        "the parser refuses, naming expected versus found - the only warning an undocumented "
        "endpoint will ever give"
        if ok
        else "NOT shown - see the FAIL lines above"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
