"""Idempotency and duplicate rejection.

This behaviour ships working. It is demonstrated as correct in phase 09, not
staged as a deliberate failure to be fixed on camera.

Doc: docs/phases/01-ingest-to-raw.md, exercise 1.2
     docs/phases/09-break-and-fix.md, "Demonstrate working, do not break"
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest
import requests

from usl import config
from usl.ingest.footystats import NoSubscriptionError, add_match_id, parse_season_matches
from usl.load.raw import backfill, raw_summary, upsert_matches

TINY_ATTENDANCE_TOTAL = 5000 + 4000 + 4500 + 3000 + 5500 + 4200


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    row = con.execute(sql, params or []).fetchone()
    assert row is not None
    return row[0]


def attendance_total(con: duckdb.DuckDBPyConnection) -> int:
    return int(scalar(con, "SELECT sum(CAST(attendance AS INTEGER)) FROM raw_matches"))


def no_network(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("the network must not be touched")


def test_second_load_inserts_nothing(
    con: duckdb.DuckDBPyConnection, tiny_raw: pd.DataFrame
) -> None:
    """Loading the same frame twice reports zero inserted and N unchanged.

    This is the log line the demo points at.
    """
    first = upsert_matches(con, tiny_raw)
    assert (first.inserted, first.updated, first.unchanged) == (6, 0, 0)

    second = upsert_matches(con, tiny_raw)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 6)
    assert scalar(con, "SELECT count(*) FROM raw_matches") == 6


def test_attendance_total_unchanged_by_a_second_load(
    con: duckdb.DuckDBPyConnection, tiny_raw: pd.DataFrame, tiny_season: pd.DataFrame
) -> None:
    """The stronger assertion.

    Row count alone is unchanged by a bug that overwrites every row with
    garbage. Identical sums plus zero inserts is hard to argue with.
    """
    assert int(tiny_season["attendance"].sum()) == TINY_ATTENDANCE_TOTAL
    upsert_matches(con, tiny_raw)
    assert attendance_total(con) == TINY_ATTENDANCE_TOTAL

    stats = upsert_matches(con, tiny_raw)

    assert stats.inserted == 0
    assert attendance_total(con) == TINY_ATTENDANCE_TOTAL
    assert scalar(con, "SELECT count(*) FROM raw_matches") == 6


def test_corrected_attendance_overwrites_the_original(
    con: duckdb.DuckDBPyConnection, tiny_raw: pd.DataFrame
) -> None:
    """Upsert, not insert-ignore.

    Sources correct attendance after the fact, so the latest figure wins. A load
    strategy that ignores conflicts would pin the first, wrong number forever.
    """
    upsert_matches(con, tiny_raw)
    corrected = tiny_raw.copy()
    corrected.loc[corrected["match_id"] == "m1", "attendance"] = "5100"

    stats = upsert_matches(con, corrected)

    assert (stats.inserted, stats.updated, stats.unchanged) == (0, 1, 5)
    assert scalar(con, "SELECT attendance FROM raw_matches WHERE match_id = 'm1'") == "5100"
    assert attendance_total(con) == TINY_ATTENDANCE_TOTAL - 5000 + 5100


def test_duplicate_match_id_in_one_batch_is_rejected(
    con: duckdb.DuckDBPyConnection, tiny_raw: pd.DataFrame, caplog: pytest.LogCaptureFixture
) -> None:
    """The primary key holds even within a single load.

    A source page that lists a match twice should not produce two rows, and
    must not fail the load either - the last row wins, and the log says so.
    """
    caplog.set_level(logging.WARNING)
    repeat = tiny_raw.iloc[[0]].copy()
    repeat.loc[:, "attendance"] = "7777"
    batch = pd.concat([tiny_raw, repeat], ignore_index=True)
    assert batch["match_id"].duplicated().sum() == 1

    stats = upsert_matches(con, batch)

    assert (stats.inserted, stats.updated, stats.unchanged) == (6, 0, 0)
    assert scalar(con, "SELECT count(*) FROM raw_matches") == 6
    assert scalar(con, "SELECT count(*) FROM raw_matches WHERE match_id = 'm1'") == 1
    assert scalar(con, "SELECT attendance FROM raw_matches WHERE match_id = 'm1'") == "7777"
    assert any(
        r.levelno == logging.WARNING and "1 row" in r.getMessage() for r in caplog.records
    ), "a dropped duplicate is worth a warning"


def test_ingested_at_alone_is_not_an_update(
    con: duckdb.DuckDBPyConnection, tiny_raw: pd.DataFrame
) -> None:
    """ingested_at changes every run; counting it would make every row 'updated'.

    It is still written - the stored stamp says when the row was last seen.
    """
    upsert_matches(con, tiny_raw)
    later = dt.datetime(2024, 3, 26, 6, 0, 0)

    stats = upsert_matches(con, tiny_raw.assign(ingested_at=later))

    assert (stats.inserted, stats.updated, stats.unchanged) == (0, 0, 6)
    assert scalar(con, "SELECT min(ingested_at) FROM raw_matches") == later


def test_missing_raw_columns_load_as_null(con: duckdb.DuckDBPyConnection) -> None:
    """A thin frame still loads; what it does not carry is NULL, and values stay text."""
    df = pd.DataFrame({"match_id": ["nk:abc"], "season_id": [7], "attendance": [1234]})

    stats = upsert_matches(con, df)

    assert stats.inserted == 1
    row = con.execute(
        "SELECT attendance, home_raw, date_unix, season_id, ingested_at FROM raw_matches"
    ).fetchone()
    assert row == ("1234", None, None, 7, None)


def test_upsert_requires_a_match_id(con: duckdb.DuckDBPyConnection, tiny_raw: pd.DataFrame) -> None:
    with pytest.raises(ValueError) as exc:
        upsert_matches(con, tiny_raw.drop(columns=["match_id"]))
    assert "match_id" in str(exc.value)


@pytest.mark.fixture_required
def test_parsed_api_frame_loads_with_api_names(
    con: duckdb.DuckDBPyConnection, example_archive_path: Path
) -> None:
    """The real response - API field names plus raw_json - lands raw and re-loads unchanged."""
    payload = json.loads(example_archive_path.read_text(encoding="utf-8"))
    df = add_match_id(parse_season_matches(payload, 1625))

    stats = upsert_matches(con, df)

    assert (stats.inserted, stats.updated, stats.unchanged) == (380, 0, 0)
    row = con.execute(
        """
        SELECT provider_id, season_id, season_raw, home_raw, away_raw, home_goals,
               away_goals, attendance, stadium_name, date_unix, source_endpoint
        FROM raw_matches WHERE match_id = 'fs:453873'
        """
    ).fetchone()
    assert row == (
        "453873",
        1625,
        "2018/2019",
        "149",
        "108",
        "2",
        "1",
        "74439",
        "Old Trafford (Manchester)",
        1533927600,
        "league-matches",
    )
    stored = scalar(con, "SELECT raw_json FROM raw_matches WHERE match_id = 'fs:453873'")
    assert json.loads(stored) == payload["data"][0], "raw_json round-trips the whole record"

    again = upsert_matches(con, df)
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 380)


@pytest.mark.fixture_required
def test_backfill_runs_from_the_archive_with_no_key(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise M1.2: the whole load, with the key removed, entirely from the archive."""
    monkeypatch.setattr(config, "FOOTYSTATS_API_KEY", "")
    monkeypatch.setattr(requests, "get", no_network)

    first = backfill(con, [1625])
    second = backfill(con, [1625])

    assert (first.inserted, first.updated, first.unchanged) == (380, 0, 0)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 380)
    assert raw_summary(con) == {
        "rows": 380,
        "seasons": [2018],
        "max_match_date": dt.date(2019, 5, 12),
        "null_attendance_pct": 0.0,
    }


@pytest.mark.fixture_required
def test_backfill_reports_completed_seasons_and_reraises(
    con: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A backfill that fails on season two says it got through season one, then fails."""
    monkeypatch.setattr(config, "FOOTYSTATS_API_KEY", "")
    monkeypatch.setattr(requests, "get", no_network)
    caplog.set_level(logging.ERROR)

    with pytest.raises(NoSubscriptionError):
        backfill(con, [1625, 424242])

    assert scalar(con, "SELECT count(*) FROM raw_matches") == 380, "the finished season stays"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("424242" in m and "1625" in m for m in errors), errors


def test_raw_summary_counts_unusable_attendance(
    con: duckdb.DuckDBPyConnection, tiny_raw: pd.DataFrame
) -> None:
    """Null, zero and 'N/A' are all 'no gate figure' - the trap the coverage script names."""
    batch = tiny_raw.copy()
    batch.loc[batch["match_id"] == "m1", "attendance"] = None
    batch.loc[batch["match_id"] == "m2", "attendance"] = "0"
    batch.loc[batch["match_id"] == "m3", "attendance"] = "N/A"
    upsert_matches(con, batch)

    summary = raw_summary(con)

    assert summary["rows"] == 6
    assert summary["seasons"] == [2024]
    assert summary["max_match_date"] == dt.date(2024, 3, 16)
    assert summary["null_attendance_pct"] == 50.0


def test_raw_summary_on_an_empty_database(con: duckdb.DuckDBPyConnection) -> None:
    assert raw_summary(con) == {
        "rows": 0,
        "seasons": [],
        "max_match_date": None,
        "null_attendance_pct": 0.0,
    }


def test_frame_with_both_api_and_raw_names_is_refused(
    con: duckdb.DuckDBPyConnection, tiny_raw: pd.DataFrame
) -> None:
    """A frame carrying homeID and home_raw cannot be reconciled silently.

    Preferring one would load the other's rows with null club ids and report
    them as clean inserts. The documented behaviour is to refuse, naming the
    pairs.
    """
    from usl.load.raw import upsert_matches

    mixed = tiny_raw.assign(homeID=[149] * len(tiny_raw))
    with pytest.raises(ValueError) as exc:
        upsert_matches(con, mixed)
    assert "homeID" in str(exc.value) and "home_raw" in str(exc.value)
