"""scripts/propose_aliases.py: provider ids to alias rows, through the name rows.

Real work against the archive, no network. The example season doubles as a
regression test on real API names: stripping the twenty numeric rows from a
copy of the CSV and running the script proposes exactly those twenty back.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from usl import config

ALIAS_HEADER = "raw_name,club_id,note\n"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "propose_aliases", config.PROJECT_ROOT / "scripts" / "propose_aliases.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve the script's postponed annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_archive(directory: Path, name: str, matches: list[dict[str, object]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps({"success": True, "data": matches}))


def _match(home_id: int, home: str, away_id: int, away: str) -> dict[str, object]:
    return {"homeID": home_id, "home_name": home, "awayID": away_id, "away_name": away}


def test_loose_key_ignores_case_punctuation_and_the_club_suffix(script: ModuleType) -> None:
    assert script.loose_key("Sacramento Republic FC") == script.loose_key("sacramento republic")
    assert script.loose_key("Brighton & Hove Albion") == script.loose_key(
        "Brighton and Hove Albion"
    )
    assert script.loose_key("Monterey Bay F.C.") == script.loose_key("Monterey Bay FC")
    assert script.loose_key("Louisville City FC") != script.loose_key("Louisville City United")


def test_proposes_matches_and_lists_the_rest(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "raw_archive"
    _write_archive(
        archive,
        "league-matches_season_id_77.json",
        [
            _match(501, "Louisville City FC", 502, "Tampa Bay Rowdies"),
            _match(503, "Unknown Town", 501, "Louisville City FC"),
            _match(504, "Sacramento Republic", 505, "Louisville City FC"),
            _match(506, "Miami FC", 502, "Tampa Bay Rowdies"),
        ],
    )
    # a .bad file beside the archive is never read
    (archive / "league-matches_season_id_78.json.bad").write_text("{not json")
    aliases = tmp_path / "club_aliases.csv"
    aliases.write_text(
        ALIAS_HEADER
        + "Louisville City FC,louisville_city,name row\n"
        + "Tampa Bay Rowdies,tampa_bay_rowdies,name row\n"
        + "Sacramento Republic FC,sacramento_republic,name row\n"
        + "Miami FC,miami_fc,name row\n"
        + "Miami,miami_other,a different club with the same loose key\n"
        + "502,tampa_bay_rowdies,already mapped\n"
        + "505,ottawa_fury,WRONG on purpose - the name says louisville\n"
    )

    code = script.main(["--aliases", str(aliases), "--archive-dir", str(archive)])
    out = capsys.readouterr().out
    assert code == 1  # something is unmatched and something conflicts
    assert "proposed:           2" in out
    assert "501,louisville_city,FootyStats club id - proposed by scripts/propose_aliases.py" in out
    assert "504,sacramento_republic," in out
    assert "id 503  Unknown Town  [no name row matches]" in out
    assert "id 506  Miami FC  [ambiguous: miami_fc, miami_other]" in out
    assert "id 505  Louisville City FC  csv=ottawa_fury  name=louisville_city" in out
    assert "dry run - nothing written" in out
    assert aliases.read_text().count("\n") == 8  # untouched


def test_write_appends_only_the_proposed_rows_and_a_rerun_is_clean(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "raw_archive"
    _write_archive(
        archive,
        "league-matches_season_id_77.json",
        [_match(501, "Louisville City FC", 504, "Sacramento Republic")],
    )
    aliases = tmp_path / "club_aliases.csv"
    aliases.write_text(
        ALIAS_HEADER
        + "Louisville City FC,louisville_city,name row\n"
        + "Sacramento Republic FC,sacramento_republic,name row\n"
    )
    assert script.main(["--aliases", str(aliases), "--archive-dir", str(archive), "--write"]) == 0
    capsys.readouterr()
    with open(aliases, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["raw_name"], r["club_id"]) for r in rows] == [
        ("Louisville City FC", "louisville_city"),
        ("Sacramento Republic FC", "sacramento_republic"),
        ("501", "louisville_city"),
        ("504", "sacramento_republic"),
    ]
    assert "league-matches_season_id_77.json" in rows[2]["note"]
    assert "," not in rows[2]["note"]

    assert script.main(["--aliases", str(aliases), "--archive-dir", str(archive)]) == 0
    out = capsys.readouterr().out
    assert "already mapped:     2" in out
    assert "proposed:           0" in out


def test_example_season_is_fully_mapped_in_the_committed_csv(
    script: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """The committed CSV and archive agree: twenty ids, all mapped, none in conflict."""
    assert script.main([]) == 0
    out = capsys.readouterr().out
    assert "provider ids seen:  20" in out
    assert "already mapped:     20" in out
    assert "conflicts:          0" in out


def test_example_season_ids_are_recovered_from_their_names(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Strip the numeric rows and the script proposes exactly them back, from real API names."""
    with open(config.CLUB_ALIASES_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    numeric = {r["raw_name"]: r["club_id"] for r in rows if r["raw_name"].isdigit()}
    assert len(numeric) == 20
    stripped = tmp_path / "club_aliases.csv"
    with open(stripped, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["raw_name", "club_id", "note"])
        writer.writeheader()
        writer.writerows(r for r in rows if not r["raw_name"].isdigit())

    assert script.main(["--aliases", str(stripped), "--write"]) == 0
    capsys.readouterr()
    with open(stripped, newline="", encoding="utf-8") as fh:
        proposed = {
            r["raw_name"]: r["club_id"] for r in csv.DictReader(fh) if r["raw_name"].isdigit()
        }
    assert proposed == numeric
