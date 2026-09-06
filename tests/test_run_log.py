"""Run and check logging.

Logging is a first-class feature here, so it gets tests: the run_log row
lifecycle (running, then success or failed, one row per stage per run), every
metadata column, the check_log upsert, the secret filter, the handler setup,
and the run context. The in-memory connection is enough for all of it - the
log tables are ordinary DuckDB tables.

Doc: docs/reference/logging-and-run-metadata.md
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import re
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pytest

from usl import config, logging_setup
from usl.db import row_count, table_exists
from usl.logging_setup import (
    LoadStats,
    RedactSecretsFilter,
    RunContext,
    configure_logging,
    current_git_sha,
    ensure_log_tables,
    finish_stage,
    log_check_result,
    new_run_context,
    stage,
    start_stage,
    utcnow,
)
from usl.transform.checks import CheckResult


def _ctx(sha: str | None = "abc1234") -> RunContext:
    """A run context without shelling out to git, so these tests are hermetic."""
    return RunContext(run_id=uuid.uuid4().hex, started_at=utcnow(), git_sha=sha)


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Fetch as dicts so a test names the column it is asserting on."""
    cur = con.execute(sql, params)
    assert cur.description is not None
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _run_rows(con: duckdb.DuckDBPyConnection, run_id: str) -> list[dict[str, Any]]:
    return _rows(con, "SELECT * FROM run_log WHERE run_id = ? ORDER BY stage", [run_id])


def _check_rows(con: duckdb.DuckDBPyConnection, run_id: str) -> list[dict[str, Any]]:
    return _rows(con, "SELECT * FROM check_log WHERE run_id = ? ORDER BY check_name", [run_id])


@pytest.fixture
def root_logging_state() -> Iterator[logging.Logger]:
    """Restore the root logger after a test that calls configure_logging.

    configure_logging installs handlers on the root logger and sets its level.
    Left in place, a file handler pointing at a deleted tmp_path would outlive
    the test.
    """
    root = logging.getLogger()
    level = root.level
    urllib3_level = logging.getLogger("urllib3").level
    try:
        yield root
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "_usl_handler", False):
                root.removeHandler(handler)
                handler.close()
        root.setLevel(level)
        logging.getLogger("urllib3").setLevel(urllib3_level)


# ---------------------------------------------------------------------------
# run_log
# ---------------------------------------------------------------------------


def test_stage_start_writes_a_running_row(con: duckdb.DuckDBPyConnection) -> None:
    """Written at start, not only at the end.

    A process killed by the OS never gets to write a terminal status. The
    leftover 'running' row is how you tell "crashed hard" from "never started".
    """
    ensure_log_tables(con)
    ctx = _ctx()

    start_stage(con, ctx, "ingest")

    rows = _run_rows(con, ctx.run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["stage"] == "ingest"
    assert row["status"] == "running"
    assert isinstance(row["started_at"], dt.datetime)
    assert row["finished_at"] is None
    assert row["duration_seconds"] is None
    assert row["error_type"] is None
    assert row["git_sha"] == "abc1234"
    # The context remembers the start so finish_stage can derive the duration.
    assert ctx.stages["ingest"]["started_at"] == row["started_at"]


def test_stage_finish_updates_rather_than_inserting(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """One row per stage per run, not two - and every metadata column lands."""
    ensure_log_tables(con)
    ctx = _ctx()
    start_stage(con, ctx, "ingest")

    finish_stage(
        con,
        ctx,
        "ingest",
        status="success",
        metadata={
            "rows_read": 380,
            "rows_inserted": np.int64(10),  # load stats arrive as numpy ints
            "rows_updated": 2,
            "rows_unchanged": 368,
            "seasons": {"2019", 2018},  # any iterable of things int() accepts
            "max_match_date": dt.date(2019, 5, 12),
            "null_attendance_pct": 0.25,
        },
    )

    rows = _run_rows(con, ctx.run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "success"
    assert isinstance(row["finished_at"], dt.datetime)
    assert row["finished_at"] >= row["started_at"]
    assert row["duration_seconds"] is not None and row["duration_seconds"] >= 0
    assert row["duration_seconds"] == pytest.approx(
        (row["finished_at"] - row["started_at"]).total_seconds()
    )
    assert (row["rows_read"], row["rows_inserted"], row["rows_updated"], row["rows_unchanged"]) == (
        380,
        10,
        2,
        368,
    )
    assert row["seasons"] == "[2018, 2019]"
    assert json.loads(row["seasons"]) == [2018, 2019]
    assert row["max_match_date"] == dt.date(2019, 5, 12)
    assert isinstance(row["max_match_date"], dt.date)
    assert row["null_attendance_pct"] == 0.25
    assert row["error_type"] is None
    assert row["error_message"] is None
    assert ctx.stages["ingest"]["status"] == "success"


@pytest.mark.parametrize(
    "value",
    [dt.date(2019, 5, 12), dt.datetime(2019, 5, 12, 19, 30), "2019-05-12"],
    ids=["date", "datetime", "iso-string"],
)
def test_max_match_date_is_stored_as_a_date(con: duckdb.DuckDBPyConnection, value: Any) -> None:
    """Whatever shape the stage reports it in, the column is a DATE."""
    ensure_log_tables(con)
    ctx = _ctx()
    with stage(con, ctx, "transform") as meta:
        meta["max_match_date"] = value
    assert _run_rows(con, ctx.run_id)[0]["max_match_date"] == dt.date(2019, 5, 12)


def test_seasons_already_serialised_are_stored_as_given(con: duckdb.DuckDBPyConnection) -> None:
    """A stage that hands over a JSON string is not double-encoded."""
    ensure_log_tables(con)
    ctx = _ctx()
    with stage(con, ctx, "transform") as meta:
        meta["seasons"] = "[2018]"
    assert _run_rows(con, ctx.run_id)[0]["seasons"] == "[2018]"


def test_failed_stage_records_the_exception(con: duckdb.DuckDBPyConnection) -> None:
    """error_type and error_message, so the log answers 'what went wrong'.

    The message is truncated: a 5000-character stack of SQL does not belong
    in a VARCHAR column that Tableau reads.
    """
    ensure_log_tables(con)
    ctx = _ctx()
    start_stage(con, ctx, "transform")

    finish_stage(con, ctx, "transform", status="failed", error=ValueError("x" * 5000))

    rows = _run_rows(con, ctx.run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "failed"
    assert row["error_type"] == "ValueError"
    assert len(row["error_message"]) == 2000
    assert row["error_message"] == "x" * 2000
    assert isinstance(row["finished_at"], dt.datetime)


def test_stage_context_manager_reraises_and_records_failed(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Never swallowed. The one rule: record it, then re-raise.

    Metadata the body filled in before it failed is kept - the row count that
    was read is part of the story of the failure.
    """
    ensure_log_tables(con)
    ctx = _ctx()

    with pytest.raises(RuntimeError, match="boom"), stage(con, ctx, "train") as meta:
        meta["rows_read"] = 12
        raise RuntimeError("boom")

    rows = _run_rows(con, ctx.run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "failed"
    assert row["error_type"] == "RuntimeError"
    assert row["error_message"] == "boom"
    assert row["rows_read"] == 12
    assert row["finished_at"] is not None


def test_failed_stage_rolls_back_an_open_transaction(con: duckdb.DuckDBPyConnection) -> None:
    """A body that dies inside BEGIN leaves the old table and a visible failed row.

    Without the rollback the UPDATE that records the failure would land inside
    the dead transaction and vanish with it, and the connection would go on
    seeing the half-done rewrite.
    """
    ensure_log_tables(con)
    ctx = _ctx()
    con.execute("CREATE TABLE t AS SELECT range AS i FROM range(5)")

    with pytest.raises(RuntimeError, match="mid-transaction"), stage(con, ctx, "transform"):
        con.execute("BEGIN")
        con.execute("CREATE OR REPLACE TABLE t AS SELECT range AS i FROM range(9)")
        assert row_count(con, "t") == 9
        raise RuntimeError("mid-transaction")

    assert row_count(con, "t") == 5
    assert _run_rows(con, ctx.run_id)[0]["status"] == "failed"
    # The connection is usable and not inside a transaction: a fresh BEGIN works.
    con.execute("BEGIN")
    con.execute("ROLLBACK")


def test_all_stages_of_a_run_share_a_run_id(con: duckdb.DuckDBPyConnection) -> None:
    """So 'every stage of last Tuesday's failed run' is one query."""
    ensure_log_tables(con)
    ctx = _ctx()

    for name, n in (("ingest", 380), ("transform", 380), ("train", 304)):
        with stage(con, ctx, name) as meta:
            meta["rows_read"] = n

    rows = _run_rows(con, ctx.run_id)
    assert [r["stage"] for r in rows] == ["ingest", "train", "transform"]
    assert {r["run_id"] for r in rows} == {ctx.run_id}
    assert all(r["status"] == "success" for r in rows)
    assert {r["stage"]: r["rows_read"] for r in rows} == {
        "ingest": 380,
        "transform": 380,
        "train": 304,
    }
    distinct = con.execute("SELECT count(DISTINCT run_id) FROM run_log").fetchone()
    assert distinct is not None and distinct[0] == 1
    assert set(ctx.stages) == {"ingest", "transform", "train"}


def test_restarting_a_stage_resets_its_row(con: duckdb.DuckDBPyConnection) -> None:
    """A stage run twice under one run_id is still one row, back to 'running'."""
    ensure_log_tables(con)
    ctx = _ctx()
    start_stage(con, ctx, "export")
    finish_stage(con, ctx, "export", status="failed", error=OSError("disk full"))

    start_stage(con, ctx, "export")

    rows = _run_rows(con, ctx.run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "running"
    assert row["finished_at"] is None
    assert row["error_type"] is None
    assert row["error_message"] is None
    assert row["duration_seconds"] is None


def test_finish_without_start_still_records_the_outcome(con: duckdb.DuckDBPyConnection) -> None:
    """A stage that skipped start_stage gets a row rather than a lost result."""
    ensure_log_tables(con)
    ctx = _ctx()

    finish_stage(con, ctx, "export", status="success", metadata={"rows_read": 3})

    rows = _run_rows(con, ctx.run_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["rows_read"] == 3
    assert isinstance(rows[0]["started_at"], dt.datetime)
    assert isinstance(rows[0]["finished_at"], dt.datetime)
    assert rows[0]["duration_seconds"] is not None


def test_finish_without_start_never_stores_a_negative_duration(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The fallback row must not carry a duration that says the stage ended before it began.

    A negative duration_seconds is not a rounding curiosity: it is the field
    the freshness monitor plots, and a row that reads 'finished before it
    started' is a row somebody will stop trusting.
    """
    ensure_log_tables(con)
    ctx = _ctx()

    finish_stage(con, ctx, "export", status="success")

    row = _run_rows(con, ctx.run_id)[0]
    assert row["finished_at"] >= row["started_at"]
    assert row["duration_seconds"] >= 0


def test_finish_rejects_an_unknown_status(con: duckdb.DuckDBPyConnection) -> None:
    """'running' is start_stage's word; a typo must not write a row nobody queries for."""
    ensure_log_tables(con)
    ctx = _ctx()
    start_stage(con, ctx, "ingest")
    with pytest.raises(ValueError, match="status must be"):
        finish_stage(con, ctx, "ingest", status="done")
    assert _run_rows(con, ctx.run_id)[0]["status"] == "running"


def test_unknown_metadata_keys_are_dropped_not_fatal(
    con: duckdb.DuckDBPyConnection, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo in a metadata key is a DEBUG line, not a failed run."""
    ensure_log_tables(con)
    ctx = _ctx()
    caplog.set_level(logging.DEBUG, logger="usl.logging_setup")

    with stage(con, ctx, "train") as meta:
        meta["rows_read"] = 1
        meta["rows_raed"] = 99

    row = _run_rows(con, ctx.run_id)[0]
    assert row["status"] == "success"
    assert row["rows_read"] == 1
    assert any("rows_raed" in r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)


def test_ensure_log_tables_is_idempotent(con: duckdb.DuckDBPyConnection) -> None:
    """Every writer calls it; the second call must not touch existing rows."""
    ensure_log_tables(con)
    ctx = _ctx()
    start_stage(con, ctx, "ingest")

    ensure_log_tables(con)

    assert table_exists(con, "run_log")
    assert table_exists(con, "check_log")
    assert len(_run_rows(con, ctx.run_id)) == 1


def test_load_stats_add_and_total() -> None:
    """Three fields, not one total; the sum of two loads is field by field."""
    a = LoadStats(inserted=5, updated=2, unchanged=10)
    b = LoadStats(inserted=1, updated=0, unchanged=3)
    total = a + b
    assert (total.inserted, total.updated, total.unchanged) == (6, 2, 13)
    assert total.total == 21
    assert LoadStats().total == 0


# ---------------------------------------------------------------------------
# check_log
# ---------------------------------------------------------------------------


def test_passing_checks_are_logged_too(con: duckdb.DuckDBPyConnection) -> None:
    """A check that has passed for six weeks and starts failing is a signal.

    Only logging failures gives you no baseline to notice that against.
    """
    ensure_log_tables(con)
    ctx = _ctx()
    passed = CheckResult("all_clubs_mapped", "staging", True, {"unmapped": [], "rows": 380})
    failed = CheckResult(
        "no_future_leakage",
        "intermediate",
        False,
        {"mismatches": ["club_a", "club_b"], "as_of": dt.date(2019, 5, 12)},
    )

    log_check_result(con, ctx, passed)
    log_check_result(con, ctx, failed)

    rows = _check_rows(con, ctx.run_id)
    assert [(r["check_name"], r["tier"], r["passed"]) for r in rows] == [
        ("all_clubs_mapped", "staging", True),
        ("no_future_leakage", "intermediate", False),
    ]
    assert json.loads(rows[0]["metadata"]) == {"rows": 380, "unmapped": []}
    # Dates are not JSON; they are stored as their string form, not dropped.
    assert json.loads(rows[1]["metadata"]) == {
        "as_of": "2019-05-12",
        "mismatches": ["club_a", "club_b"],
    }
    assert all(isinstance(r["checked_at"], dt.datetime) for r in rows)
    assert all(r["run_id"] == ctx.run_id for r in rows)


def test_logging_the_same_check_twice_for_one_run_upserts(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A tier rebuilt within one run rewrites its check rows rather than duplicating them."""
    ensure_log_tables(con)
    ctx = _ctx()

    log_check_result(con, ctx, CheckResult("features_not_null", "mart", True, {"nulls": 0}))
    log_check_result(con, ctx, CheckResult("features_not_null", "mart", False, {"nulls": 4}))

    rows = _check_rows(con, ctx.run_id)
    assert len(rows) == 1
    assert rows[0]["passed"] is False
    assert json.loads(rows[0]["metadata"]) == {"nulls": 4}

    # A different run is a different row: the key is (run_id, check_name).
    other = _ctx()
    log_check_result(con, other, CheckResult("features_not_null", "mart", True, {}))
    total = con.execute("SELECT count(*) FROM check_log").fetchone()
    assert total is not None and total[0] == 2


def test_check_result_levels_follow_the_outcome(
    con: duckdb.DuckDBPyConnection, caplog: pytest.LogCaptureFixture
) -> None:
    """A pass is INFO narrative; a failure is an ERROR, rare enough to mean something."""
    ensure_log_tables(con)
    ctx = _ctx()
    caplog.set_level(logging.INFO, logger="usl.logging_setup")

    log_check_result(con, ctx, CheckResult("a_pass", "staging", True, {}))
    log_check_result(con, ctx, CheckResult("a_fail", "staging", False, {"why": "x"}))

    levels = {r.getMessage().split()[1]: r.levelno for r in caplog.records if "check" in r.msg}
    assert levels == {"a_pass": logging.INFO, "a_fail": logging.ERROR}


# ---------------------------------------------------------------------------
# Secrets never reach a log line
# ---------------------------------------------------------------------------


def _record(msg: str, *args: Any) -> logging.LogRecord:
    return logging.LogRecord("usl.test", logging.INFO, __file__, 1, msg, args or None, None)


def test_redact_filter_scrubs_the_configured_key_and_any_key_parameter() -> None:
    """Both guards: the literal secret wherever it appears, and key=... whatever the value."""
    flt = RedactSecretsFilter(secret="s3cr3t-key")

    # The secret arrives through %-args, which is how a real log call carries a URL.
    rec = _record(
        "GET %s failed after %s", "https://api.example/x?key=s3cr3t-key&season_id=1", "s3cr3t-key"
    )
    assert flt.filter(rec) is True
    text = rec.getMessage()
    assert "s3cr3t-key" not in text
    assert "key=***" in text
    assert "season_id=1" in text
    assert text.endswith("failed after ***")

    # A key= parameter with some other value is scrubbed too, case-insensitively,
    # and stops at the next delimiter.
    rec = _record("url ?KEY=other-secret&x=1 and 'key=quoted' and key=last")
    flt.filter(rec)
    assert rec.getMessage() == "url ?KEY=***&x=1 and 'key=***' and key=***"

    # A record with nothing to scrub is left alone, args included.
    rec = _record("loaded %d rows", 380)
    flt.filter(rec)
    assert rec.msg == "loaded %d rows"
    assert rec.args == (380,)
    assert rec.getMessage() == "loaded 380 rows"


def test_redact_filter_defaults_to_the_configured_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No argument means the key from config, so the CLI cannot forget to pass it."""
    monkeypatch.setattr(config, "FOOTYSTATS_API_KEY", "from-config-abc")
    flt = RedactSecretsFilter()
    assert flt.secret == "from-config-abc"

    rec = _record("token from-config-abc seen")
    flt.filter(rec)
    assert rec.getMessage() == "token *** seen"

    # An empty key (archive-only mode) must not turn into replace('', '***').
    monkeypatch.setattr(config, "FOOTYSTATS_API_KEY", "")
    rec = _record("plain line")
    RedactSecretsFilter().filter(rec)
    assert rec.getMessage() == "plain line"


def test_redact_filter_scrubs_what_a_handler_writes() -> None:
    """End to end through a handler: the formatted line on disk is what matters."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter(logging_setup.LOG_FORMAT))
    handler.addFilter(RedactSecretsFilter(secret="paid-credential"))
    logger = logging.getLogger("usl.test.redact")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        logger.debug("fetching %s", "https://api.example/league-matches?key=paid-credential&id=1")
    finally:
        logger.removeHandler(handler)
        handler.close()

    written = buffer.getvalue()
    assert "paid-credential" not in written
    assert "key=***&id=1" in written
    assert "usl.test.redact" in written


# ---------------------------------------------------------------------------
# configure_logging and the run context
# ---------------------------------------------------------------------------


def test_configure_logging_is_idempotent_and_writes_a_file(
    root_logging_state: logging.Logger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling it twice replaces its handlers rather than stacking them.

    Stacked handlers double every line, and a second file handler would leave
    a run split across two files under logs/.
    """
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    root = root_logging_state

    configure_logging(logging.INFO, run_id="abcdef0123456789")
    configure_logging(logging.INFO, run_id="abcdef0123456789")

    usl_handlers = [h for h in root.handlers if getattr(h, "_usl_handler", False)]
    assert len(usl_handlers) == 2
    file_handlers = [h for h in usl_handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
    assert root.level == logging.INFO
    assert logging.getLogger("urllib3").level == logging.WARNING

    log_file = Path(file_handlers[0].baseFilename)
    assert log_file.parent == tmp_path / "logs"
    assert log_file.suffix == ".log"
    assert "_abcdef01" in log_file.name  # the first eight characters of the run_id

    logging.getLogger("usl.test").info("hello from the run log test key=%s", "topsecret")
    for handler in usl_handlers:
        handler.flush()
    written = log_file.read_text(encoding="utf-8")
    assert written.count("hello from the run log test") == 1
    assert "topsecret" not in written
    assert "key=***" in written
    assert "INFO" in written and "usl.test" in written


def test_configure_logging_without_a_run_id(
    root_logging_state: logging.Logger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dated filename stands on its own before a run context exists."""
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")

    configure_logging(logging.DEBUG)

    files = list((tmp_path / "logs").glob("*.log"))
    assert len(files) == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}\.log", files[0].name)
    assert root_logging_state.level == logging.DEBUG


def test_new_run_context_has_a_hex_run_id_and_a_short_sha() -> None:
    """A uuid4 hex string, a naive UTC start, and whatever git can say about HEAD."""
    ctx = new_run_context()

    assert re.fullmatch(r"[0-9a-f]{32}", ctx.run_id)
    assert ctx.git_sha is None or re.fullmatch(r"[0-9a-f]{4,40}", ctx.git_sha)
    assert ctx.started_at.tzinfo is None
    assert abs((utcnow() - ctx.started_at).total_seconds()) < 60
    assert ctx.stages == {}
    assert new_run_context().run_id != ctx.run_id


def test_git_sha_degrades_to_none_rather_than_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading the sha never fails the run: no git, or not a repo, is None."""

    def no_git(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    monkeypatch.setattr(logging_setup.subprocess, "run", no_git)
    assert current_git_sha() is None
    assert new_run_context().git_sha is None

    def not_a_repo(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: not a git repo")

    monkeypatch.setattr(logging_setup.subprocess, "run", not_a_repo)
    assert current_git_sha() is None

    def a_repo(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="0123abc\n", stderr="")

    monkeypatch.setattr(logging_setup.subprocess, "run", a_repo)
    assert current_git_sha() == "0123abc"


def test_utcnow_is_naive_utc() -> None:
    """TIMESTAMP columns hold naive values; the convention is that they are UTC."""
    now = utcnow()
    assert now.tzinfo is None
    aware = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    assert abs((aware - now).total_seconds()) < 5
