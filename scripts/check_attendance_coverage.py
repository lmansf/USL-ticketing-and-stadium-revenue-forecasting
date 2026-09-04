#!/usr/bin/env python3
"""Does the FootyStats API carry per-match attendance, and how completely?

THIS IS A REAL SCRIPT, NOT A STUB. It is the one thing in this repo that has to
work before you spend money, because it answers the question the whole project
rests on: attendance is the target variable, and if the API does not carry it,
there is nothing to predict.

Run it free, before subscribing:

    python scripts/check_attendance_coverage.py

That uses the public "example" key and the English Premier League 2018/19 season
to answer the weaker question - does the field exist at all, anywhere.

Then, once subscribed, run it against a real USL season to answer the question
that actually matters - is it populated for THIS league:

    python scripts/check_attendance_coverage.py --season-id 1234

Read the verdict carefully. There are three outcomes, not two: the field can be
absent, present and populated, or present and mostly empty. The third is the
most likely and the most dangerous, because a naive check reports success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://api.football-data-api.com"
EXAMPLE_KEY = "example"
EXAMPLE_SEASON_ID = 1625  # EPL 2018/19, served by the example key

# Field names that plausibly carry a per-match gate figure. The API's naming is
# not documented for this, so cast a wide net and report whatever is found.
CANDIDATES = ("attendance", "crowd", "spectators", "attendance_count")

# Below this share of matches carrying a usable figure, the API cannot be your
# only attendance source.
USABLE_THRESHOLD = 0.80


def fetch(endpoint: str, key: str, **params: object) -> dict:
    """GET one endpoint and return the parsed body."""
    query = urllib.parse.urlencode({"key": key, **params})
    url = f"{BASE_URL}/{endpoint}?{query}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def find_attendance_fields(match: dict) -> list[str]:
    """Return field names on a match record that look like attendance."""
    return [k for k in match if any(c in k.lower() for c in CANDIDATES)]


def is_populated(value: object) -> bool:
    """Whether a value is a usable gate figure rather than a null placeholder.

    APIs signal "no data" as 0, -1, "", None, and "N/A" more or less
    interchangeably, and a naive `is not None` check counts all of those as
    present. That is exactly how you end up believing you have attendance data
    and discovering in week three that it is 6000 zeroes.
    """
    if value is None or value == "":
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--season-id",
        type=int,
        default=EXAMPLE_SEASON_ID,
        help="FootyStats season id. Defaults to the free EPL 2018/19 example season.",
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("FOOTYSTATS_API_KEY") or EXAMPLE_KEY,
        help="API key. Defaults to FOOTYSTATS_API_KEY, then the public example key.",
    )
    args = parser.parse_args()

    using_example = args.key == EXAMPLE_KEY
    print(f"season_id={args.season_id}  key={'example (free)' if using_example else 'yours'}")
    print()

    try:
        payload = fetch("league-matches", args.key, season_id=args.season_id)
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic script
        print(f"REQUEST FAILED: {exc}")
        print()
        print("A 401 means the key is wrong or the subscription has lapsed.")
        print("A 404 usually means the season id is wrong - get ids from league-list.")
        print("A 403 on a tunnel/proxy line is your network blocking the host, not")
        print("the API rejecting you - try from an unproxied connection.")
        return 2

    matches = payload.get("data") or []
    if not matches:
        print("No matches in the response. Check the season id.")
        print(f"Top-level keys: {sorted(payload)}")
        return 2

    print(f"{len(matches)} matches returned.")

    fields = find_attendance_fields(matches[0])
    if not fields:
        print()
        print("VERDICT: NO attendance field on the league-matches record.")
        print()
        print("Before concluding the API cannot supply attendance, try the")
        print("undocumented match-detail endpoint for a single match - it returns")
        print("more per-match fields than the season listing does.")
        print()
        print("Sample of available fields:")
        for name in sorted(matches[0])[:40]:
            print(f"    {name}")
        return 1

    print(f"Candidate field(s): {fields}")
    print()

    exit_code = 0
    for field in fields:
        values = [m.get(field) for m in matches]
        populated = [v for v in values if is_populated(v)]
        share = len(populated) / len(values)
        print(f"  {field}: {len(populated)}/{len(values)} populated ({share:.0%})")
        if populated:
            nums = sorted(float(v) for v in populated)
            median = nums[len(nums) // 2]
            print(f"    min {nums[0]:,.0f}   median {median:,.0f}   max {nums[-1]:,.0f}")
        if share < USABLE_THRESHOLD:
            exit_code = 1

    print()
    if exit_code == 0:
        print(f"VERDICT: attendance is present and populated (>={USABLE_THRESHOLD:.0%}).")
        if using_example:
            print()
            print("NOTE: this was the EPL. It proves the field exists, NOT that it is")
            print("populated for USL Championship. Coverage of gate figures is usually")
            print("far better for major European leagues than for anywhere else.")
            print("Re-run against a real USL season id on day one of your subscription,")
            print("BEFORE you delete any fallback ingest path.")
    else:
        print(f"VERDICT: attendance is too sparse to rely on (<{USABLE_THRESHOLD:.0%}).")
        print()
        print("Sanity-check the median above against what you know these clubs draw.")
        print("A median of 0, or a suspiciously round number, means the field exists")
        print("but is a placeholder rather than data.")
        print()
        print("If this is a USL season, you need a second attendance source. The")
        print("scraper was removed in favour of the API - recover it with:")
        print("    git log --oneline --diff-filter=D -- usl/scrape/")
        print("    git checkout <sha>^ -- usl/scrape/ tests/test_parse.py")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
