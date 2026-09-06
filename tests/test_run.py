"""The CLI: exit codes, the run log, and the weekly run.

usl/run.py is what the scheduler calls, and its exit code is the scheduler's
only view of the run. These tests pin the contract from the module docstring -
0 green, 1 a stage or check failed, 3 the database stayed locked - together
with the one run_id shared by every stage of a weekly invocation, stopping at
the first failure, and the archive-only ingest branch.

Everything runs in-process through main([...]) against a database under
tmp_path, served from the committed example-season archive with no key. One
loaded database is built per module and copied per test, so the whole file
costs a few seconds.

Doc: docs/mvp/05-mvp-schedule.md
     docs/reference/logging-and-run-metadata.md
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from usl import config
from usl.run import EXIT_FAILED, EXIT_LOCKED, EXIT_OK, main
from usl.transform import checks, reference

SEASONS_WITH_EXAMPLE = (
    "season,season_id,note\n"
    f"2018,{config.EXAMPLE_SEASON_ID},the example season\n"
    "2019,,not pulled yet\n"
)
SEASONS_WITH_NO_IDS = "season,season_id,note\n2019,,not pulled yet\n2020,,not pulled yet\n"

FAST_XGB: dict[str, object] = {
    "n_estimators": 20,
    "max_depth": 3,
    "learning_rate": 0.1,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
}

# Holds the database file open for writing until killed. argv[1] is the path.
_HOLDER = """
import sys, time
import duckdb
# The handle is kept in a name on purpose: an unreferenced one is collected, and the lock with it.
con = duckdb.connect(sys.argv[1])
print("held", flush=True)
time.sleep(120)
"""


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _isolate(mp: pytest.MonkeyPatch, tmp: Path) -> None:
    """Point every path and secret the CLI reads at the test's own directory."""
    mp.setattr(config, "LOG_DIR", tmp / "logs")
    mp.setattr(config, "FOOTYSTATS_API_KEY", "")
    mp.setattr(config, "CURRENT_SEASON", None)
    mp.setattr(config, "XGB_PARAMS", dict(FAST_XGB))
    mp.setattr(config, "VARIANCE_SEEDS", (42,))
    seasons = tmp / "seasons.csv"
    seasons.write_text(SEASONS_WITH_EXAMPLE, encoding="utf-8")
    mp.setattr(config, "SEASONS_CSV", seasons)


def _drop_cli_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_usl_handler", False):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Per-test isolation: paths, key, fast training, and the CLI's log handlers."""
    level = logging.getLogger().level
    _isolate(monkeypatch, tmp_path)
    try:
        yield
    finally:
        _drop_cli_handlers()
        logging.getLogger().setLevel(level)


@pytest.fixture(scope="module")
def loaded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database with the example season backfilled and transformed, built once."""
    tmp = tmp_path_factory.mktemp("cli")
    mp = pytest.MonkeyPatch()
    _isolate(mp, tmp)
    try:
        path = tmp / "loaded.duckdb"
        assert main(["backfill", "--db", str(path)]) == EXIT_OK
        assert main(["transform", "--db", str(path)]) == EXIT_OK
    finally:
        _drop_cli_handlers()
        mp.undo()
    return path


@pytest.fixture
def db(loaded_db: Path, tmp_path: Path) -> Path:
    """A private copy of the loaded database for tests that write to it."""
    copy = tmp_path / "usl_test.duckdb"
    shutil.copy(loaded_db, copy)
    return copy


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _run_log(path: Path) -> list[dict[str, Any]]:
    """Every run_log row, oldest first, as dicts."""
    with duckdb.connect(str(path), read_only=True) as con:
        rel = con.execute("SELECT * FROM run_log ORDER BY started_at, stage")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]


def _latest_run(path: Path) -> dict[str, dict[str, Any]]:
    """The rows of the most recently started run, keyed by stage."""
    rows = _run_log(path)
    newest = max(rows, key=lambda r: r["started_at"])["run_id"]
    return {r["stage"]: r for r in rows if r["run_id"] == newest}


# --------------------------------------------------------------------------
# Backfill and transform
# --------------------------------------------------------------------------


def test_backfill_from_the_archive_records_the_load(loaded_db: Path) -> None:
    """Exit 0, and the run_log row carries the split and the freshness fields."""
    rows = [r for r in _run_log(loaded_db) if r["stage"] == "backfill"]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "success"
    assert row["rows_read"] == 380
    assert row["rows_inserted"] == 380
    assert row["rows_updated"] == 0
    assert row["rows_unchanged"] == 0
    assert row["seasons"] == "[2018]"
    assert str(row["max_match_date"]) == "2019-05-12"
    assert row["null_attendance_pct"] == 0.0
    assert row["error_type"] is None
    assert row["duration_seconds"] >= 0


def test_second_backfill_reports_everything_unchanged(db: Path) -> None:
    """The idempotency guard, seen from the scheduler's side."""
    assert main(["backfill", "--db", str(db)]) == EXIT_OK
    row = _latest_run(db)["backfill"]
    assert (row["rows_inserted"], row["rows_updated"], row["rows_unchanged"]) == (0, 0, 380)


def test_transform_logs_every_check_and_passes(loaded_db: Path) -> None:
    """One check_log row per check, passes included, all green on the example season."""
    expected = {
        c.__name__ for c in checks.STAGING_CHECKS + checks.INTERMEDIATE_CHECKS + checks.MART_CHECKS
    }
    run_id = _latest_run(loaded_db)["transform"]["run_id"]
    with duckdb.connect(str(loaded_db), read_only=True) as con:
        rows = con.execute(
            "SELECT check_name, passed FROM check_log WHERE run_id = ?", [run_id]
        ).fetchall()
    assert {name for name, _ in rows} == expected
    assert all(passed for _, passed in rows)


def test_transform_before_backfill_fails_legibly(tmp_path: Path) -> None:
    """An empty database is a failed stage that names the command to run, not a crash."""
    path = tmp_path / "empty.duckdb"
    assert main(["transform", "--db", str(path)]) == EXIT_FAILED
    row = _latest_run(path)["transform"]
    assert row["status"] == "failed"
    assert row["error_type"] == "ConfigurationError"
    assert "backfill" in row["error_message"]


def test_backfill_with_no_season_ids_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A seasons.csv with nothing to pull is an error, not an empty success."""
    seasons = tmp_path / "blank.csv"
    seasons.write_text(SEASONS_WITH_NO_IDS, encoding="utf-8")
    monkeypatch.setattr(config, "SEASONS_CSV", seasons)
    path = tmp_path / "x.duckdb"
    assert main(["backfill", "--db", str(path)]) == EXIT_FAILED
    row = _latest_run(path)["backfill"]
    assert row["status"] == "failed"
    assert row["error_type"] == "ConfigurationError"
    assert "season_id" in row["error_message"]


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


def test_ingest_archive_only_records_zero_rows(db: Path, caplog: pytest.LogCaptureFixture) -> None:
    """With no current season the stage succeeds with zero rows and says why."""
    caplog.set_level(logging.INFO, logger="usl.run")
    assert main(["ingest", "--db", str(db)]) == EXIT_OK
    row = _latest_run(db)["ingest"]
    assert row["status"] == "success"
    assert (row["rows_read"], row["rows_inserted"], row["rows_unchanged"]) == (0, 0, 0)
    assert "archive-only" in caplog.text


def test_ingest_current_season_serves_the_archive_and_warns(
    db: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A current season with no key is served from the archive, loudly."""
    monkeypatch.setattr(config, "CURRENT_SEASON", 2018)
    caplog.set_level(logging.WARNING, logger="usl.run")
    assert main(["ingest", "--db", str(db)]) == EXIT_OK
    row = _latest_run(db)["ingest"]
    assert row["status"] == "success"
    assert row["rows_unchanged"] == 380
    assert "cannot reach the API" in caplog.text


def test_ingest_current_season_without_an_id_fails(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A current season whose seasons.csv row has no id is a named configuration error."""
    monkeypatch.setattr(config, "CURRENT_SEASON", 2019)
    assert main(["ingest", "--db", str(db)]) == EXIT_FAILED
    row = _latest_run(db)["ingest"]
    assert row["status"] == "failed"
    assert row["error_type"] == "ConfigurationError"
    assert "2019" in row["error_message"]


# --------------------------------------------------------------------------
# Weekly
# --------------------------------------------------------------------------


def test_weekly_shares_one_run_id_across_all_five_stages(db: Path, tmp_path: Path) -> None:
    """One invocation, one run_id, five success rows, and the extracts on disk."""
    out = tmp_path / "extracts"
    assert main(["weekly", "--db", str(db), "--out-dir", str(out)]) == EXIT_OK
    run = _latest_run(db)
    assert set(run) == {"ingest", "weather", "transform", "train", "export"}
    assert {r["status"] for r in run.values()} == {"success"}
    assert len({r["run_id"] for r in run.values()}) == 1
    assert run["train"]["rows_read"] == 380
    assert run["export"]["rows_read"] > 0
    assert (out / "predictions_with_band.csv").exists()
    assert (out / "model_metrics.csv").exists()


def test_weekly_stops_at_the_first_failed_stage(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken alias fails transform; train and export never run for that run_id."""
    broken = tmp_path / "club_aliases.csv"
    original = config.CLUB_ALIASES_CSV.read_text(encoding="utf-8")
    assert "\n93,manchester_city," in original
    broken.write_text(original.replace("\n93,manchester_city,", "\n9393,manchester_city,"), "utf-8")
    monkeypatch.setitem(reference.REFERENCE_CSVS, "club_aliases", broken)

    assert main(["weekly", "--db", str(db), "--out-dir", str(tmp_path / "x")]) == EXIT_FAILED
    run = _latest_run(db)
    assert set(run) == {"ingest", "weather", "transform"}
    assert run["ingest"]["status"] == "success"
    assert run["transform"]["status"] == "failed"
    assert run["transform"]["error_type"] == "CheckFailure"
    assert "all_clubs_mapped" in run["transform"]["error_message"]


# --------------------------------------------------------------------------
# The lock, and the commands that do not open the database
# --------------------------------------------------------------------------


def test_locked_database_exits_3(db: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Scenario D1 from the scheduler's side: exit 3 and a log line naming the holder."""
    caplog.set_level(logging.ERROR, logger="usl.run")
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(db)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held", holder.stderr and holder.stderr.read()
        code = main(["transform", "--db", str(db), "--lock-attempts", "1"])
    finally:
        holder.kill()
        holder.wait(timeout=30)
    assert code == EXIT_LOCKED
    assert "locked by another process" in caplog.text
    assert f"PID {holder.pid}" in caplog.text


def test_league_list_without_a_key_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    """No key and no archived response: a named refusal, no network call, exit 1."""
    assert main(["league-list"]) == EXIT_FAILED
    assert "needs a key" in capsys.readouterr().err


def test_archive_command_reports_the_example_season(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The archive summary names what is pulled and what is not."""
    assert main(["archive"]) == EXIT_OK
    out = capsys.readouterr().out
    assert str(config.EXAMPLE_SEASON_ID) in out
    assert "not pulled yet" in out
    assert "2019" in out
    assert "QUARANTINED" not in out


def test_archive_command_flags_a_quarantined_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A .bad file is a spent request that is never served; the summary says so."""
    sandbox = tmp_path / "raw_archive"
    sandbox.mkdir()
    (sandbox / "league-matches_season_id_1625.json.bad").write_text('{"success": false}')
    monkeypatch.setattr(config, "ARCHIVE_DIR", sandbox)
    assert main(["archive"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "QUARANTINED: 1 .bad file(s)" in out
    assert "files:      0" in out
