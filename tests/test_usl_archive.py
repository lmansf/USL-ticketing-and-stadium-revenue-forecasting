"""The committed USL Championship archive, end to end.

Nine seasons from data/raw_archive/ into an in-memory database through the
real loader and the real SQL layer, every check green, and the reconstructed
tables agreeing with the provider's published ones club for club. Built once
per session; the assertions pin what the data actually contains, so a change
to a reference file or a model that alters the USL result is caught here.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from collections.abc import Iterator
from types import ModuleType

import duckdb
import pytest

from usl import config
from usl.load.raw import backfill
from usl.transform.runner import run_sql_layer

USL_SEASONS = {
    2017: 1291,
    2018: 1290,
    2019: 1857,
    2020: 3710,
    2021: 6541,
    2022: 6970,
    2023: 8845,
    2024: 17351,
    2025: 13967,
}


@pytest.fixture(scope="module")
def usl(tmp_path_factory: pytest.TempPathFactory) -> Iterator[duckdb.DuckDBPyConnection]:
    """The nine archived USL seasons, loaded and transformed once."""
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "FOOTYSTATS_API_KEY", "")
    mp.setattr(config, "CURRENT_SEASON", None)
    mp.setattr(config, "SEASONS_CSV", config.REF_DIR / "seasons.csv")
    con = duckdb.connect(":memory:")
    try:
        stats = backfill(con, sorted(USL_SEASONS.values()))
        assert stats.inserted == 4203
        con.execute("CREATE TABLE _counts AS SELECT 1")
        counts = run_sql_layer(con)
        con.execute("DROP TABLE _counts")
        con.execute("CREATE TABLE _layer_counts (model VARCHAR, rows INTEGER)")
        for model, rows in counts.items():
            con.execute("INSERT INTO _layer_counts VALUES (?, ?)", [model, rows])
        yield con
    finally:
        con.close()
        mp.undo()


def test_every_season_loads_and_every_check_passes(usl: duckdb.DuckDBPyConnection) -> None:
    counts = dict(usl.execute("SELECT model, rows FROM _layer_counts").fetchall())
    assert counts["stg_matches"] == 4203
    assert counts["mart_match_features"] == 4194  # nine void: eight cancelled, one abandoned
    assert counts["int_standings"] == counts["int_stakes"]
    seasons = dict(usl.execute("SELECT season, count(*) FROM stg_matches GROUP BY 1").fetchall())
    assert seasons == {
        2017: 495,
        2018: 576,
        2019: 631,
        2020: 296,
        2021: 511,
        2022: 472,
        2023: 423,
        2024: 423,
        2025: 376,
    }
    void = dict(
        usl.execute("SELECT season, count(*) FROM stg_matches WHERE is_void GROUP BY 1").fetchall()
    )
    assert void == {2020: 7, 2021: 1, 2025: 1}
    playoffs = dict(
        usl.execute(
            "SELECT season, count(*) FROM stg_matches WHERE is_playoff GROUP BY 1"
        ).fetchall()
    )
    assert playoffs == {
        2017: 15,
        2018: 15,
        2019: 19,
        2020: 15,
        2021: 15,
        2022: 13,
        2023: 15,
        2024: 15,
        2025: 15,
    }


def test_attendance_coverage_is_what_the_archive_holds(usl: duckdb.DuckDBPyConnection) -> None:
    """The gate is on 94-100 percent of 2017-2019, none of 2021-2023, 57-61 percent of 2024-2025."""
    rows = usl.execute(
        "SELECT season, count(*) FILTER (WHERE attendance IS NOT NULL), count(*) "
        "FROM mart_match_features WHERE is_played GROUP BY 1 ORDER BY 1"
    ).fetchall()
    share = {season: round(with_gate / played, 2) for season, with_gate, played in rows}
    assert share[2017] == 1.0 and share[2018] >= 0.97 and share[2019] >= 0.93
    assert share[2021] == 0.0 and share[2022] == 0.0 and share[2023] == 0.0
    assert 0.55 <= share[2024] <= 0.6 and 0.6 <= share[2025] <= 0.62
    labelled = usl.execute(
        "SELECT count(*) FROM mart_match_features WHERE is_played AND attendance IS NOT NULL"
    ).fetchone()
    assert labelled is not None and 2100 <= labelled[0] <= 2160


def test_reconstructed_tables_match_the_published_ones(usl: duckdb.DuckDBPyConnection) -> None:
    """Every club-season agrees with the provider's table, regular season and playoffs included."""
    spec = importlib.util.spec_from_file_location(
        "verify_standings", config.PROJECT_ROOT / "scripts" / "verify_standings.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    report = module.compare(usl, USL_SEASONS, config.CLUB_ALIASES_CSV)
    assert report["mismatches"] == 0
    assert report["all_clubs"] == 264
    assert (
        report["group_clubs"] == 181
    )  # 2017-2019 and 2021-2023: the seasons whose groups carry rows


def test_playoff_rows_read_the_final_table(usl: duckdb.DuckDBPyConnection) -> None:
    """A playoff row is live, has no fixtures remaining, and reads a rank from the final table."""
    row = usl.execute(
        "SELECT count(*), bool_and(is_mathematically_live), max(matches_remaining), "
        "count(*) FILTER (WHERE rank_before IS NULL), min(matches_since_elimination) "
        "FROM mart_match_features WHERE is_playoff"
    ).fetchone()
    assert row == (135, True, 0, 0, -1)  # 137 playoff rows in staging, two of them void


def test_lag_history_does_not_cross_the_empty_seasons(usl: duckdb.DuckDBPyConnection) -> None:
    """No 2024 match carries a lag from 2019: the history restarted after the hole."""
    stale = usl.execute(
        """
        WITH last_gate AS (
            SELECT home_club_id, max(date) AS d FROM stg_matches
            WHERE season <= 2020 AND attendance IS NOT NULL GROUP BY 1
        )
        SELECT count(*) FROM mart_match_features m
        JOIN last_gate g USING (home_club_id)
        WHERE m.season = 2024 AND m.last_home_gate IS NOT NULL
          AND m.date - g.d > ? AND m.is_season_opener
        """,
        [config.LAG_MAX_GAP_DAYS],
    ).fetchone()
    assert stale == (0,)
    openers_null = usl.execute(
        "SELECT count(*) FROM mart_match_features WHERE season = 2024 AND is_season_opener "
        "AND last_home_gate IS NULL"
    ).fetchone()
    assert openers_null is not None and openers_null[0] >= 19


def test_weather_for_every_usl_ground_is_archived(
    usl: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refresh serves every USL club-day from the archived files: no request, nothing missing.

    Runs last in the module on purpose: it repopulates raw_weather and rebuilds
    staging on the shared connection.
    """
    from usl.weather import open_meteo, refresh

    def no_network(url: str, params: dict[str, object]) -> str:
        raise AssertionError(f"a request left the archive: {url} {params}")

    monkeypatch.setattr(open_meteo, "_request", no_network)
    monkeypatch.setattr(config, "WEATHER_ENABLED", True)
    stats = refresh.refresh(usl, today=dt.date(2026, 9, 7))
    assert stats.archive_replayed == 53  # 52 clubs, Louisville twice (two grounds)
    assert stats.archive_requests == 0
    assert stats.forecast_requests == 0
    assert stats.club_days_needed == 4194
    assert stats.club_days_missing == 0
    assert stats.no_stadium == 0
