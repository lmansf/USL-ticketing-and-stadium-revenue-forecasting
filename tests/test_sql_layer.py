"""SQL layer: ordering, rebuild idempotency, tiers and checks, and staging types.

The runner tests here point usl.transform.reference.REFERENCE_CSVS at CSVs
written from the tiny fixtures under tmp_path, so run_sql_layer reads
reference files exactly the way it does in production - through
read_reference_csv - without depending on the real ones.

Doc: docs/phases/05-sql-layer.md
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from conftest import stage_frames

from usl import config
from usl.config import SQL_DIR
from usl.db import table_exists
from usl.load.raw import RAW_COLUMNS, ensure_raw_tables
from usl.logging_setup import ensure_log_tables, new_run_context
from usl.transform import reference, runner
from usl.transform.checks import (
    INTERMEDIATE_CHECKS,
    MART_CHECKS,
    STAGING_CHECKS,
    CheckFailure,
    matches_are_fresh,
    one_row_per_match,
)
from usl.transform.runner import MODELS, TIER_ORDER, TIERS

ALL_CHECKS = STAGING_CHECKS + INTERMEDIATE_CHECKS + MART_CHECKS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_reference_csvs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    aliases: pd.DataFrame,
    club_rows: pd.DataFrame,
) -> None:
    """Write the tiny reference frames as CSVs and point the runner at them."""
    structure = (
        club_rows[["season", "conference"]]
        .drop_duplicates()
        .assign(playoff_spots=2, relegation_spots=1, note="test structure")
    )
    frames = {
        "club_aliases": aliases,
        "club_conference": club_rows.assign(note="test"),
        "conference_structure": structure,
        "derbies": pd.DataFrame(
            [("club_a", "club_b", "test")], columns=["club_id_a", "club_id_b", "note"]
        ),
        "stadiums": pd.DataFrame(
            [("club_a", "Ground A", 27.9, -82.4, "2017-01-01", "2099-12-31", "test")],
            columns=["club_id", "stadium", "lat", "lon", "valid_from", "valid_to", "note"],
        ),
    }
    for name, frame in frames.items():
        path = tmp_path / f"{name}.csv"
        frame.to_csv(path, index=False)
        monkeypatch.setitem(reference.REFERENCE_CSVS, name, path)


def load_raw(con: duckdb.DuckDBPyConnection, raw: pd.DataFrame) -> None:
    """Build raw_matches from a tiny_raw-shaped frame."""
    ensure_raw_tables(con)
    con.register("raw_frame", raw)
    con.execute(f"INSERT INTO raw_matches SELECT {', '.join(RAW_COLUMNS)} FROM raw_frame")
    con.unregister("raw_frame")


def table_contents(con: duckdb.DuckDBPyConnection, name: str) -> list[tuple[object, ...]]:
    """Every row of a table in a canonical order, for equality comparison."""
    return con.execute(f'SELECT * FROM "{name}" ORDER BY ALL').fetchall()


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def test_models_are_declared_in_dependency_order() -> None:
    """Staging before intermediate before mart.

    An explicit list rather than an inferred graph, so a reader can check it -
    and so this test can.
    """
    assert MODELS.index("stg_clubs") < MODELS.index("stg_matches")
    assert MODELS.index("stg_matches") < MODELS.index("int_standings")
    assert MODELS.index("int_standings") < MODELS.index("int_stakes")
    assert MODELS.index("int_stakes") < MODELS.index("mart_match_features")
    assert MODELS.index("mart_match_features") < MODELS.index("mart_decay_curve")


def test_every_model_has_a_sql_file() -> None:
    """A name in MODELS with no file fails at run time, in the middle of a run."""
    missing = [m for m in MODELS if not (SQL_DIR / f"{m}.sql").exists()]
    assert not missing, f"declared models with no .sql file: {missing}"


def test_every_sql_file_is_a_single_select() -> None:
    """The runner wraps each file in CREATE OR REPLACE TABLE; the file must not."""
    for model in MODELS:
        text = (SQL_DIR / f"{model}.sql").read_text(encoding="utf-8")
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
        assert body.strip().upper().startswith(("SELECT", "WITH")), model
        assert "CREATE " not in body.upper(), model


def test_tiers_cover_every_model_in_order() -> None:
    """Every model has a tier, and MODELS never steps back to an earlier tier."""
    assert set(TIERS) == set(MODELS)
    assert set(TIERS.values()) <= set(TIER_ORDER)
    positions = [TIER_ORDER.index(TIERS[m]) for m in MODELS]
    assert positions == sorted(positions)


def test_materialise_rejects_an_undeclared_model(con: duckdb.DuckDBPyConnection) -> None:
    """Only declared models are built - the name goes straight into DDL."""
    with pytest.raises(ValueError, match="not a declared model"):
        runner.materialise(con, "raw_matches")
    with pytest.raises(ValueError):
        runner.materialise(con, "stg_matches; DROP TABLE raw_matches")


def test_materialise_returns_the_row_count_and_logs_it(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The per-model count is the second signal against silent data loss."""
    stage_frames(con, tiny_season, tiny_clubs)
    with caplog.at_level(logging.INFO, logger="usl.transform.runner"):
        n = runner.materialise(con, "int_standings")
    assert n == 16
    assert "materialised int_standings rows=16" in caplog.text
    # and it is a full rebuild: a second call replaces, not appends
    assert runner.materialise(con, "int_standings") == 16


# ---------------------------------------------------------------------------
# Reference tables
# ---------------------------------------------------------------------------


def test_load_reference_tables_returns_counts_and_builds_ref_config(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """Every CSV under REFERENCE_CSVS lands as an all-VARCHAR table, plus ref_config."""
    write_reference_csvs(tmp_path, monkeypatch, aliases=club_aliases, club_rows=tiny_clubs)
    counts = runner.load_reference_tables(con)
    assert counts == {
        "club_aliases": 5,
        "club_conference": 4,
        "conference_structure": 1,
        "derbies": 1,
        "stadiums": 1,
    }
    types = {r[0]: r[1] for r in con.execute("DESCRIBE conference_structure").fetchall()}
    assert set(types.values()) == {"VARCHAR"}
    cfg = con.execute("SELECT match_tz, covid_start, covid_end FROM ref_config").fetchall()
    assert cfg == [(config.MATCH_TZ, config.COVID_START, config.COVID_END)]


def test_reference_csvs_parse_with_their_declared_columns(con: duckdb.DuckDBPyConnection) -> None:
    """The real files under usl/ref/ load under exactly the columns their header declares.

    DuckDB's dialect sniffer falls back to a single column when the rows carry
    unquoted commas, and every join against that table then fails to bind with
    an error that names a column rather than the file. This names the file.
    """
    for name, path in reference.REFERENCE_CSVS.items():
        declared = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        reference.read_reference_csv(con, name, path)
        found = [r[0] for r in con.execute(f'DESCRIBE "{name}"').fetchall()]
        assert found == declared, (
            f"{path.name} loaded with columns {found} instead of its header {declared}. "
            "A value containing a comma must be quoted (RFC 4180); check the note column."
        )


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def test_rerunning_the_layer_produces_identical_tables(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """CREATE OR REPLACE means a full rebuild, so twice equals once.

    Note this is the opposite of raw_matches, which upserts because it must not
    lose history the source no longer serves. Raw accumulates; everything below
    it is derived and disposable. Compared on row counts and on full contents.
    """
    write_reference_csvs(tmp_path, monkeypatch, aliases=club_aliases, club_rows=tiny_clubs)
    load_raw(con, tiny_raw)
    first_counts = runner.run_sql_layer(con)
    first = {m: table_contents(con, m) for m in MODELS}
    second_counts = runner.run_sql_layer(con)
    second = {m: table_contents(con, m) for m in MODELS}
    assert first_counts == second_counts
    assert first_counts == {
        "stg_clubs": 4,
        "stg_matches": 6,
        "int_standings": 16,
        "int_stakes": 16,
        "mart_match_features": 6,
        "mart_decay_curve": 0,
    }
    for model in MODELS:
        assert first[model] == second[model], model


def test_failing_check_stops_before_the_next_tier(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """Collect within a tier, stop between tiers.

    There is no value in computing a mart on staging you already know is broken -
    it just produces a second wave of failures that are downstream artefacts of
    the first. With 'Club C' missing from the alias file the staging tier fails
    naming that string, and int_standings is never built.
    """
    without_c = club_aliases[club_aliases["raw_name"] != "Club C"]
    write_reference_csvs(tmp_path, monkeypatch, aliases=without_c, club_rows=tiny_clubs)
    load_raw(con, tiny_raw)
    with pytest.raises(CheckFailure) as excinfo:
        runner.run_sql_layer(con)
    message = str(excinfo.value)
    assert message.startswith("1 check(s) failed in staging: ['all_clubs_mapped']")
    assert "Club C" in message
    assert "\n" not in message  # one line, so it reads in the run log
    assert table_exists(con, "stg_matches")
    assert not table_exists(con, "int_standings")
    assert not table_exists(con, "mart_match_features")


def test_two_failures_in_one_tier_are_reported_together(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """One run tells you everything wrong at a level, not one thing per run."""
    without_c = club_aliases[club_aliases["raw_name"] != "Club C"]
    three_clubs = tiny_clubs[tiny_clubs["club_id"] != "club_d"]
    write_reference_csvs(tmp_path, monkeypatch, aliases=without_c, club_rows=three_clubs)
    load_raw(con, tiny_raw)
    with pytest.raises(CheckFailure) as excinfo:
        runner.run_sql_layer(con)
    message = str(excinfo.value)
    assert message.startswith(
        "2 check(s) failed in staging: ['all_clubs_mapped', 'all_club_seasons_have_conference']"
    )
    assert "club_d" in message


def test_all_check_results_are_logged_not_only_failures(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """A check that only writes a row when it fails gives you no baseline."""
    write_reference_csvs(tmp_path, monkeypatch, aliases=club_aliases, club_rows=tiny_clubs)
    load_raw(con, tiny_raw)
    ctx = new_run_context()
    ensure_log_tables(con)
    runner.run_sql_layer(con, ctx)
    logged = con.execute(
        "SELECT check_name, tier, passed FROM check_log WHERE run_id = ? ORDER BY checked_at",
        [ctx.run_id],
    ).fetchall()
    assert len(logged) == len(ALL_CHECKS) == 10
    assert [name for name, _, _ in logged] == [c.__name__ for c in ALL_CHECKS]
    assert all(passed for _, _, passed in logged)
    assert [tier for _, tier, _ in logged] == ["staging"] * 7 + ["intermediate"] + ["mart"] * 2


def test_failed_checks_are_logged_and_later_tiers_are_not_run(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """The failing run leaves the staging results in check_log, failure included."""
    without_c = club_aliases[club_aliases["raw_name"] != "Club C"]
    write_reference_csvs(tmp_path, monkeypatch, aliases=without_c, club_rows=tiny_clubs)
    load_raw(con, tiny_raw)
    ctx = new_run_context()
    with pytest.raises(CheckFailure):
        runner.run_sql_layer(con, ctx)  # ensure_log_tables is the runner's job here
    logged = dict(
        con.execute(
            "SELECT check_name, passed FROM check_log WHERE run_id = ?", [ctx.run_id]
        ).fetchall()
    )
    assert len(logged) == len(STAGING_CHECKS)
    assert logged["all_clubs_mapped"] is False
    assert logged["row_count_preserved"] is True
    assert "no_future_leakage" not in logged


def test_one_row_per_match_fires_on_a_duplicated_match_id(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A repeated match_id in staging is named, with how many rows carry it.

    The raw primary key makes this impossible from a single load, so the case
    it guards is a staging join that fanned out or a second source keyed
    differently. The check must fire on the duplicate and pass once it is gone.
    """
    stage_frames(con, tiny_season, tiny_clubs)
    assert one_row_per_match(con).passed
    con.execute("INSERT INTO stg_matches SELECT * FROM stg_matches WHERE match_id = 'm3'")
    result = one_row_per_match(con)
    assert not result.passed
    assert result.tier == "staging"
    assert result.metadata == {"n_duplicated": 1, "duplicates": [{"match_id": "m3", "rows": 2}]}


def test_run_checks_logs_to_the_stream_without_a_context(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without a run context the results still reach the log, at INFO for a pass."""
    stage_frames(con, tiny_season, tiny_clubs)
    with caplog.at_level(logging.INFO, logger="usl.transform.runner"):
        results = runner.run_checks(con, (one_row_per_match,))
    assert [r.name for r in results] == ["one_row_per_match"]
    assert results[0].passed
    record = next(r for r in caplog.records if "one_row_per_match" in r.getMessage())
    assert record.levelno == logging.INFO
    assert "passed" in record.getMessage()


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_freshness_passes_on_archive_only_data(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no current season configured nothing could be fresh, and the check says why."""
    monkeypatch.setattr(config, "CURRENT_SEASON", None)
    stage_frames(con, tiny_season, tiny_clubs)
    result = matches_are_fresh(con)
    assert result.passed
    assert result.metadata == {"reason": "archive-only: no current season configured"}


def test_freshness_is_gated_on_the_season(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale in season fails; the same gap in the off-season is correct.

    The latest played match in tiny_season is 2024-03-16. Four days later it
    is fresh; six weeks later, still inside the March-to-November season, it
    is stale; the following January the same data passes because an eighty-day
    gap in winter is what normal looks like.
    """
    monkeypatch.setattr(config, "CURRENT_SEASON", 2024)
    monkeypatch.setattr(config, "MAX_MATCH_AGE_DAYS", 10)
    stage_frames(con, tiny_season, tiny_clubs)

    fresh = matches_are_fresh(con, today=dt.date(2024, 3, 20))
    assert fresh.passed
    assert fresh.metadata["latest_match"] == "2024-03-16"
    assert (fresh.metadata["age_days"], fresh.metadata["in_season"]) == (4, True)

    stale = matches_are_fresh(con, today=dt.date(2024, 4, 30))
    assert not stale.passed
    assert (stale.metadata["age_days"], stale.metadata["in_season"]) == (45, True)

    winter = matches_are_fresh(con, today=dt.date(2025, 1, 15))
    assert winter.passed
    assert winter.metadata["in_season"] is False


def test_freshness_with_no_match_in_the_current_season(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured season with nothing played yet is stale in season, fine outside it."""
    monkeypatch.setattr(config, "CURRENT_SEASON", 2025)
    stage_frames(con, tiny_season, tiny_clubs)
    in_season = matches_are_fresh(con, today=dt.date(2025, 6, 1))
    assert not in_season.passed
    assert in_season.metadata["latest_match"] is None
    assert in_season.metadata["age_days"] is None
    assert matches_are_fresh(con, today=dt.date(2025, 1, 15)).passed


# ---------------------------------------------------------------------------
# Staging types, through the real stg_matches.sql
# ---------------------------------------------------------------------------


def test_staging_types_and_calendar_columns(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """Raw text becomes typed staging columns, and only played matches carry results.

    Four extra raw rows exercise the edges: an unplayed fixture with empty
    scores, and three played matches whose attendance the source rendered as
    0, -1, and 'N/A' - none of which is a gate, so all three become NULL.
    """
    template = tiny_raw.iloc[0].to_dict()
    extra = []
    for i, (status, hg, ag, att, day) in enumerate(
        [
            ("incomplete", "", "", "", 23),
            ("complete", "1", "0", "0", 24),
            ("complete", "1", "0", "-1", 25),
            ("complete", "1", "0", "N/A", 26),
        ],
        start=7,
    ):
        kickoff = dt.datetime(2024, 3, day, 19, 30, tzinfo=dt.UTC)
        extra.append(
            {
                **template,
                "match_id": f"m{i}",
                "provider_id": str(i),
                "date_unix": int(kickoff.timestamp()),
                "status": status,
                "home_raw": "Club A",
                "away_raw": "Club B",
                "home_goals": hg,
                "away_goals": ag,
                "attendance": att,
            }
        )
    raw = pd.concat([tiny_raw, pd.DataFrame(extra)], ignore_index=True)
    write_reference_csvs(tmp_path, monkeypatch, aliases=club_aliases, club_rows=tiny_clubs)
    runner.load_reference_tables(con)
    load_raw(con, raw)
    runner.materialise(con, "stg_clubs")
    runner.materialise(con, "stg_matches")

    types = {r[0]: r[1] for r in con.execute("DESCRIBE stg_matches").fetchall()}
    assert types["season"] == "INTEGER"
    assert types["date"] == "DATE"
    assert types["kickoff_utc"] == "TIMESTAMP"
    assert types["home_goals"] == "INTEGER"
    assert types["attendance"] == "INTEGER"
    assert types["is_played"] == "BOOLEAN"
    assert types["day_of_week"] == "INTEGER"

    cols = [r[0] for r in con.execute("DESCRIBE stg_matches").fetchall()]
    rows = {
        r[0]: dict(zip(cols, r, strict=True))
        for r in con.execute("SELECT * FROM stg_matches").fetchall()
    }
    assert list(rows) == [f"m{i}" for i in range(1, 11)]  # ordered by date, match_id

    m1 = rows["m1"]
    assert m1["season"] == 2024
    assert m1["season_id"] == 999
    assert m1["date"] == dt.date(2024, 3, 2)
    assert m1["kickoff_utc"] == dt.datetime(2024, 3, 2, 12, 0)
    assert m1["is_played"] is True
    assert (m1["home_goals"], m1["away_goals"], m1["attendance"]) == (2, 0, 5000)
    assert (m1["day_of_week"], m1["month"]) == (6, 3)  # Saturday, 0 = Sunday
    assert (m1["is_weekend"], m1["is_midweek"]) == (True, False)
    assert m1["is_covid_affected"] is False

    unplayed = rows["m7"]
    assert unplayed["is_played"] is False
    assert (unplayed["home_goals"], unplayed["away_goals"], unplayed["attendance"]) == (
        None,
        None,
        None,
    )
    assert unplayed["home_club_id"] == "club_a"  # still mapped, still present
    assert (unplayed["day_of_week"], unplayed["is_weekend"]) == (6, True)
    assert rows["m8"]["attendance"] is None  # 0
    assert rows["m9"]["attendance"] is None  # -1
    assert rows["m10"]["attendance"] is None  # N/A
    assert rows["m9"]["is_played"] is True
    # midweek: 2024-03-26 is a Tuesday
    assert (rows["m10"]["day_of_week"], rows["m10"]["is_midweek"]) == (2, True)


def test_match_date_is_taken_in_the_configured_timezone(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiny_raw: pd.DataFrame,
    club_aliases: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """The match DATE follows config.MATCH_TZ; the kick-off stays in UTC.

    A 7:30pm Pacific Saturday kick-off is 02:30 UTC on Sunday. In UTC (the
    default, exact for the example season) that is day_of_week 0 and a
    weekend; in America/Los_Angeles it is Saturday, 6, still a weekend, but
    the date - which every lag and standings join keys on - is a day earlier.
    This is the judgement call build-decisions.md flags for USL, and the test
    shows the one-row ref_config table is where it takes effect.
    """
    raw = tiny_raw.copy()
    kickoff = dt.datetime(2024, 3, 3, 2, 30, tzinfo=dt.UTC)  # Sunday 02:30 UTC
    raw.loc[raw["match_id"] == "m1", "date_unix"] = int(kickoff.timestamp())
    write_reference_csvs(tmp_path, monkeypatch, aliases=club_aliases, club_rows=tiny_clubs)
    load_raw(con, raw)

    def m1() -> tuple[object, ...]:
        runner.load_reference_tables(con)  # rebuilds ref_config from config.MATCH_TZ
        runner.materialise(con, "stg_clubs")
        runner.materialise(con, "stg_matches")
        row = con.execute(
            "SELECT date, day_of_week, is_weekend, kickoff_utc FROM stg_matches "
            "WHERE match_id = 'm1'"
        ).fetchone()
        assert row is not None
        return row

    monkeypatch.setattr(config, "MATCH_TZ", "UTC")
    assert m1() == (dt.date(2024, 3, 3), 0, True, dt.datetime(2024, 3, 3, 2, 30))
    monkeypatch.setattr(config, "MATCH_TZ", "America/Los_Angeles")
    assert m1() == (dt.date(2024, 3, 2), 6, True, dt.datetime(2024, 3, 3, 2, 30))
