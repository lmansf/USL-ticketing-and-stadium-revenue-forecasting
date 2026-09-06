#!/usr/bin/env python3
"""Propose the provider-id rows for club_aliases.csv from the archived responses.

The join in stg_matches is on the provider's numeric club id, and those ids are
not knowable until a season has been pulled. What IS knowable in advance is the
club's name, so usl/ref/club_aliases.csv carries a name row per club before the
subscription starts. This script closes the gap on the day the first USL season
lands in data/raw_archive/:

    python scripts/propose_aliases.py            # dry run: prints the rows it would add
    python scripts/propose_aliases.py --write    # appends them to club_aliases.csv

It reads every archived league-matches response, collects each (homeID,
home_name) and (awayID, away_name) pair, and for every id not yet in the CSV
looks the name up against the name rows - loosely (case, punctuation, and a
trailing FC or SC do not matter), because a person reviews the output before it
is written and the join itself stays exact. An id whose name matches nothing,
or matches two clubs, is listed for mapping by hand; an id already in the CSV
whose name points at a different club is flagged as a conflict and never
written. The exit code is 1 while anything is unmatched or in conflict, so the
subscription-month loop is: run, resolve the printed list, run again.

Runs against the archive only. No key, no request, nothing spent.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from usl import config  # noqa: E402
from usl.transform.reference import normalize_club_key  # noqa: E402

# Tokens dropped when matching a provider name to a name row. Only the club
# type suffixes: anything more (dropping "City", "United") collides clubs.
_DROPPED_TOKENS = frozenset({"fc", "sc", "f.c.", "s.c."})
_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")


def loose_key(name: object) -> str:
    """The comparison form of a club name for PROPOSALS only.

    Lower-case, punctuation removed, whitespace collapsed, and a leading or
    trailing FC or SC dropped. Never used in the join - see phase 03, exercise
    3.2 - because it collides "Miami FC" with "Miami" on purpose, and that is
    fine for a suggestion a person reads and wrong for a key.

    Args:
        name: A display name from the API or from the CSV.

    Returns:
        The loose key, possibly empty.
    """
    text = normalize_club_key(name).lower().replace("&", "and")
    text = _PUNCTUATION.sub("", text)
    tokens = [t for t in text.split() if t not in _DROPPED_TOKENS]
    return " ".join(tokens)


@dataclass
class Sighting:
    """One provider club id as seen across the archive."""

    provider_id: str
    names: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)


@dataclass
class Proposal:
    """The outcome of one run: what to add, what to look at."""

    proposed: list[tuple[str, str, str]] = field(default_factory=list)  # raw_name, club_id, note
    already_mapped: list[str] = field(default_factory=list)
    unmatched: list[tuple[Sighting, str]] = field(default_factory=list)  # sighting, reason
    conflicts: list[tuple[Sighting, str, str]] = field(default_factory=list)  # csv id, name id


def read_aliases(path: Path) -> list[dict[str, str]]:
    """The alias rows, normalised the way the loader normalises them."""
    with open(path, encoding="utf-8", newline="") as fh:
        return [
            {k: normalize_club_key(v) if v is not None else "" for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]


def collect_sightings(archive_dir: Path) -> dict[str, Sighting]:
    """Every (provider id, name) pair in the archived league-matches responses.

    Args:
        archive_dir: The archive directory. Partial and quarantined files do
            not end in .json and are never read.

    Returns:
        Sightings keyed by provider id (as text - the join key is text).
    """
    sightings: dict[str, Sighting] = {}
    for path in sorted(archive_dir.glob("league-matches*.json")):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        matches = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, dict):
                continue
            for id_key, name_key in (("homeID", "home_name"), ("awayID", "away_name")):
                raw_id = match.get(id_key)
                if raw_id is None or raw_id == "":
                    continue
                provider_id = normalize_club_key(raw_id)
                sighting = sightings.setdefault(provider_id, Sighting(provider_id))
                name = match.get(name_key)
                if name:
                    sighting.names.add(normalize_club_key(name))
                sighting.files.add(path.name)
    return sightings


def propose(aliases: list[dict[str, str]], sightings: dict[str, Sighting]) -> Proposal:
    """Match each sighted id to a club_id through the name rows.

    Args:
        aliases: Rows of club_aliases.csv.
        sightings: From collect_sightings.

    Returns:
        The proposal. Nothing is written here.
    """
    mapped: dict[str, str] = {row["raw_name"]: row["club_id"] for row in aliases if row["club_id"]}
    by_loose: dict[str, set[str]] = {}
    for row in aliases:
        if row["club_id"] and not row["raw_name"].isdigit():
            by_loose.setdefault(loose_key(row["raw_name"]), set()).add(row["club_id"])

    result = Proposal()
    for provider_id, sighting in sorted(
        sightings.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0
    ):
        candidates: set[str] = set()
        for name in sighting.names:
            candidates |= by_loose.get(loose_key(name), set())
        if provider_id in mapped:
            if candidates and mapped[provider_id] not in candidates:
                result.conflicts.append(
                    (sighting, mapped[provider_id], ", ".join(sorted(candidates)))
                )
            else:
                result.already_mapped.append(provider_id)
            continue
        if not sighting.names:
            result.unmatched.append((sighting, "no name in the response"))
        elif not candidates:
            result.unmatched.append((sighting, "no name row matches"))
        elif len(candidates) > 1:
            result.unmatched.append((sighting, f"ambiguous: {', '.join(sorted(candidates))}"))
        else:
            club_id = next(iter(candidates))
            names = " / ".join(sorted(sighting.names))
            files = " ".join(sorted(sighting.files))
            note = (
                "FootyStats club id - proposed by scripts/propose_aliases.py "
                f"from {files} (name {names})"
            )
            result.proposed.append((provider_id, club_id, note.replace(",", ";")))
    return result


def append_rows(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """Append proposed rows to the CSV, one line each, nothing quoted."""
    with open(path, "a", encoding="utf-8", newline="") as fh:
        for raw_name, club_id, note in rows:
            fh.write(f"{raw_name},{club_id},{note}\n")


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Command line, without the program name.

    Returns:
        0 when every sighted id is mapped or proposed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="append the proposed rows to the CSV")
    parser.add_argument("--aliases", type=Path, default=config.CLUB_ALIASES_CSV)
    parser.add_argument("--archive-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    archive_dir = args.archive_dir or config.ARCHIVE_DIR

    aliases = read_aliases(args.aliases)
    sightings = collect_sightings(archive_dir)
    result = propose(aliases, sightings)

    print(f"archive: {archive_dir}")
    print(f"  provider ids seen:  {len(sightings)}")
    print(f"  already mapped:     {len(result.already_mapped)}")
    print(f"  proposed:           {len(result.proposed)}")
    print(f"  unmatched:          {len(result.unmatched)}")
    print(f"  conflicts:          {len(result.conflicts)}")
    if result.proposed:
        print()
        print("proposed rows (raw_name,club_id,note):")
        for raw_name, club_id, note in result.proposed:
            print(f"  {raw_name},{club_id},{note}")
    if result.unmatched:
        print()
        print("unmatched - map these by hand (add a name row, then run again):")
        for sighting, reason in result.unmatched:
            names = " / ".join(sorted(sighting.names)) or "<no name>"
            where = " ".join(sorted(sighting.files))
            print(f"  id {sighting.provider_id}  {names}  [{reason}]  in {where}")
    if result.conflicts:
        print()
        print("conflicts - the CSV maps the id to one club and the name to another; fix the CSV:")
        for sighting, csv_club, name_club in result.conflicts:
            names = " / ".join(sorted(sighting.names))
            print(f"  id {sighting.provider_id}  {names}  csv={csv_club}  name={name_club}")
    if args.write and result.proposed:
        append_rows(args.aliases, result.proposed)
        print()
        print(f"appended {len(result.proposed)} row(s) to {args.aliases}")
    elif result.proposed:
        print()
        print("dry run - nothing written; re-run with --write to append")
    return 1 if result.unmatched or result.conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
