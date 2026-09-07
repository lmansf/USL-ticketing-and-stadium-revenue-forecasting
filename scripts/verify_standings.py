#!/usr/bin/env python3
"""Check the reconstructed standings against the provider's published tables.

    python scripts/verify_standings.py                      # every USL season in the archive
    python scripts/verify_standings.py --db data/usl.duckdb  # an existing database

int_standings is rebuilt from match results, and the one thing that can go
wrong silently is a table that is nearly right. The archived league-tables
responses are the published answer, so this compares every club-season two
ways and reports any difference:

  1. The regular-season group tables, where the provider has them (points and
     matches played per club), against the final snapshot row of int_standings.
  2. The provider's all-matches table, which includes playoff points, against
     the final snapshot plus the points and matches from the playoff rows.

Both must agree club for club. With no --db the seasons are loaded from the
archive into an in-memory database, which takes a few seconds and needs no key.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.propose_aliases import loose_key  # noqa: E402
from usl import config  # noqa: E402
from usl.ingest import archive  # noqa: E402
from usl.load.raw import backfill  # noqa: E402
from usl.transform.runner import run_sql_layer  # noqa: E402

_PLAYOFF_POINTS_SQL = """
    WITH sides AS (
        SELECT season, home_club_id AS club_id,
               CASE WHEN home_goals > away_goals THEN 3
                    WHEN home_goals = away_goals THEN 1 ELSE 0 END AS pts
        FROM stg_matches WHERE is_playoff AND is_played
        UNION ALL
        SELECT season, away_club_id,
               CASE WHEN away_goals > home_goals THEN 3
                    WHEN away_goals = home_goals THEN 1 ELSE 0 END
        FROM stg_matches WHERE is_playoff AND is_played
    )
    SELECT season, club_id, sum(pts), count(*) FROM sides GROUP BY season, club_id
"""

_FINAL_TABLE_SQL = """
    SELECT club_id, pts_before, played_before
    FROM int_standings s
    WHERE season = ?
      AND date = (SELECT max(date) FROM int_standings
                  WHERE season = ? AND conference = s.conference)
"""


def alias_maps(path: Path) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Provider id to club_id, and loose name to club_ids, from club_aliases.csv."""
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_id = {r["raw_name"].strip(): r["club_id"] for r in rows if r["raw_name"].strip().isdigit()}
    by_loose: dict[str, set[str]] = {}
    for r in rows:
        if r["club_id"] and not r["raw_name"].strip().isdigit():
            by_loose.setdefault(loose_key(r["raw_name"]), set()).add(r["club_id"])
    return by_id, by_loose


def archived_seasons() -> dict[int, int]:
    """season year -> season id for every seasons.csv row with an archived league table."""
    out = {}
    for row in config.read_seasons_csv():
        if row.season_id is None:
            continue
        if archive.is_archived("league-tables", {"season_id": row.season_id}):
            out[row.season] = row.season_id
    return out


def compare(
    con: duckdb.DuckDBPyConnection, seasons: dict[int, int], aliases_csv: Path
) -> dict[str, Any]:
    """Compare every season; return counts and the mismatches by season."""
    by_id, by_loose = alias_maps(aliases_csv)
    playoff: dict[tuple[int, str], tuple[int, int]] = {
        (int(r[0]), str(r[1])): (int(r[2]), int(r[3]))
        for r in con.execute(_PLAYOFF_POINTS_SQL).fetchall()
    }
    report: dict[str, Any] = {"seasons": {}, "group_clubs": 0, "all_clubs": 0, "mismatches": 0}
    for year, sid in sorted(seasons.items()):
        payload = archive.read_archived("league-tables", {"season_id": sid})["data"]
        ours = {
            str(r[0]): (int(r[1]), int(r[2]))
            for r in con.execute(_FINAL_TABLE_SQL, [year, year]).fetchall()
        }
        groups: dict[str, tuple[int, int]] = {}
        unresolved: list[str] = []
        for table in payload.get("specific_tables") or []:
            if table.get("round") != "Regular Season":
                continue
            for group in table.get("groups") or []:
                for row in group.get("table") or []:
                    name = row.get("cleanName") or row.get("name")
                    ids = by_loose.get(loose_key(name), set())
                    if len(ids) != 1:
                        unresolved.append(str(name))
                        continue
                    groups[next(iter(ids))] = (int(row["points"]), int(row["matchesPlayed"]))
        group_bad = {c: (ours.get(c), v) for c, v in groups.items() if ours.get(c) != v}
        overall: dict[str, tuple[int, int]] = {}
        for row in payload.get("all_matches_table_overall") or []:
            club = by_id.get(str(row.get("id")))
            if club is None:
                unresolved.append(str(row.get("cleanName") or row.get("id")))
                continue
            overall[club] = (int(row["points"]), int(row["matchesPlayed"]))
        overall_bad = {}
        for club, expected in overall.items():
            regular = ours.get(club, (0, 0))
            extra = playoff.get((year, club), (0, 0))
            got = (regular[0] + extra[0], regular[1] + extra[1])
            if got != expected:
                overall_bad[club] = (got, expected)
        report["seasons"][year] = {
            "group_clubs": len(groups),
            "group_mismatches": group_bad,
            "all_clubs": len(overall),
            "all_mismatches": overall_bad,
            "unresolved_names": sorted(set(unresolved)),
        }
        report["group_clubs"] += len(groups)
        report["all_clubs"] += len(overall)
        report["mismatches"] += len(group_bad) + len(overall_bad) + len(set(unresolved))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=None, help="an existing database to check")
    parser.add_argument("--aliases", type=Path, default=config.CLUB_ALIASES_CSV)
    args = parser.parse_args(argv)

    seasons = archived_seasons()
    if not seasons:
        print("verify_standings: no season in seasons.csv has an archived league-tables response")
        return 2
    if args.db is not None:
        con = duckdb.connect(str(args.db), read_only=True)
    else:
        con = duckdb.connect(":memory:")
        backfill(con, sorted(seasons.values()))
        run_sql_layer(con)
    try:
        report = compare(con, seasons, args.aliases)
    finally:
        con.close()

    for year, info in report["seasons"].items():
        status = (
            "ok"
            if not (info["group_mismatches"] or info["all_mismatches"] or info["unresolved_names"])
            else "MISMATCH"
        )
        print(
            f"{year}: {status}  regular-season table: {info['group_clubs']} club(s) checked, "
            f"{len(info['group_mismatches'])} differ; "
            f"regular+playoff: {info['all_clubs']} checked, "
            f"{len(info['all_mismatches'])} differ"
        )
        for club, (got, expected) in {**info["group_mismatches"], **info["all_mismatches"]}.items():
            print(f"    {club}: ours {got} published {expected}")
        for name in info["unresolved_names"]:
            print(f"    unresolved name in the published table: {name!r}")
    print(
        f"{report['all_clubs']} club-seasons against the published totals, "
        f"{report['group_clubs']} against regular-season tables, "
        f"{report['mismatches']} mismatch(es)"
    )
    return 1 if report["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
