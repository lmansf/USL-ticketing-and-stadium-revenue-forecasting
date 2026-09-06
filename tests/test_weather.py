"""Phase two weather: the Open-Meteo client, the refresh, the join, and the checks.

The network is never touched. usl.weather.open_meteo._request is replaced by
a scripted fake that returns Open-Meteo-shaped bodies, and the archive lives
under tmp_path. Everything the refresh writes is then read back through the
real stg_weather.sql and the real mart, so the join and the feature columns
are the ones the pipeline uses.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from tests.conftest import stage_frames, with_unplayed
from usl import config
from usl.features.definitions import WEATHER_FEATURES, mart_columns
from usl.transform import runner
from usl.transform.checks import (
    features_not_null,
    home_matches_resolve_to_one_stadium,
    played_weather_is_observed,
)
from usl.weather import open_meteo, refresh

TODAY = dt.date(2024, 3, 20)

STADIUM_COLUMNS = ["club_id", "stadium", "lat", "lon", "valid_from", "valid_to", "note"]


def daily_body(start: dt.date, days: int, *, base_temp: float = 10.0) -> str:
    """An Open-Meteo daily response for `days` days from start."""
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    return json.dumps(
        {
            "latitude": 40.0,
            "longitude": -80.0,
            "daily_units": {"temperature_2m_max": "°C"},
            "daily": {
                "time": [d.isoformat() for d in dates],
                "temperature_2m_max": [base_temp + i for i in range(days)],
                "temperature_2m_min": [base_temp - 5 + i for i in range(days)],
                "precipitation_sum": [0.0 if i % 2 else 1.5 for i in range(days)],
                "wind_speed_10m_max": [12.0 + i for i in range(days)],
                "cloud_cover_mean": [50.0 for _ in range(days)],
            },
        }
    )


class FakeRequest:
    """A scripted stand-in for open_meteo._request that records every call.

    With auto=True and nothing scripted it answers from the request itself:
    the archive range or the forecast horizon asked for, one row per day.
    """

    def __init__(
        self, *bodies: str | Exception, auto: bool = False, today: dt.date = TODAY
    ) -> None:
        self.bodies = list(bodies)
        self.auto = auto
        self.today = today  # a forecast request does not carry its date; the fake needs it
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, params: dict[str, Any]) -> str:
        self.calls.append((url, dict(params)))
        if not self.bodies:
            if not self.auto:
                raise AssertionError("open_meteo._request was called more times than scripted")
            if "start_date" in params:
                start = dt.date.fromisoformat(params["start_date"])
                end = dt.date.fromisoformat(params["end_date"])
                return daily_body(start, (end - start).days + 1)
            return daily_body(self.today, int(params["forecast_days"]))
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return body


REAL_REQUEST = open_meteo._request


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRequest:
    """Archive under tmp_path, weather enabled, no network unless scripted."""
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "raw_archive")
    monkeypatch.setattr(config, "WEATHER_ENABLED", True)
    fake = FakeRequest()
    monkeypatch.setattr(open_meteo, "_request", fake)
    monkeypatch.setattr(open_meteo, "_sleep", lambda _: None)
    return fake


def script(fake: FakeRequest, *bodies: str | Exception) -> None:
    fake.bodies.extend(bodies)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


def test_archive_response_is_archived_then_served_without_a_request(sandbox: FakeRequest) -> None:
    """Historical weather never changes, so the second call is an archive hit."""
    script(sandbox, daily_body(dt.date(2024, 3, 1), 3))
    frame, name = open_meteo.fetch_archive(40.0, -80.0, dt.date(2024, 3, 1), dt.date(2024, 3, 3))
    assert len(sandbox.calls) == 1
    url, params = sandbox.calls[0]
    assert url == open_meteo.ARCHIVE_URL
    assert params["daily"] == ",".join(open_meteo.DAILY_FIELDS)
    assert params["start_date"] == "2024-03-01" and params["end_date"] == "2024-03-03"
    assert (config.ARCHIVE_DIR / name).exists()
    assert name.startswith("open-meteo-archive_")
    assert list(frame["date"]) == [dt.date(2024, 3, 1), dt.date(2024, 3, 2), dt.date(2024, 3, 3)]
    assert set(frame["weather_source"]) == {"archive"}
    assert frame["forecast_horizon_days"].isna().all()
    assert list(frame.columns) == [
        "date",
        *open_meteo.DAILY_FIELDS,
        "weather_source",
        "forecast_horizon_days",
    ]

    again, _ = open_meteo.fetch_archive(40.0, -80.0, dt.date(2024, 3, 1), dt.date(2024, 3, 3))
    assert len(sandbox.calls) == 1
    pd.testing.assert_frame_equal(again, frame)


def test_coordinates_are_rounded_into_one_archive_key(sandbox: FakeRequest) -> None:
    """51.5549000001 and 51.5549 are the same place and the same cached file."""
    script(sandbox, daily_body(dt.date(2024, 3, 1), 1))
    _, first = open_meteo.fetch_archive(
        51.5549000001, -0.10840001, dt.date(2024, 3, 1), dt.date(2024, 3, 1)
    )
    _, second = open_meteo.fetch_archive(51.5549, -0.1084, dt.date(2024, 3, 1), dt.date(2024, 3, 1))
    assert first == second
    assert len(sandbox.calls) == 1


def test_error_body_is_quarantined_and_never_served(sandbox: FakeRequest) -> None:
    """An Open-Meteo error envelope goes to .bad; the next call requests again."""
    script(sandbox, json.dumps({"error": True, "reason": "Parameter 'start_date' is invalid"}))
    with pytest.raises(open_meteo.OpenMeteoError, match="reported an error"):
        open_meteo.fetch_archive(40.0, -80.0, dt.date(2024, 3, 1), dt.date(2024, 3, 3))
    files = sorted(p.name for p in config.ARCHIVE_DIR.iterdir())
    assert len(files) == 1 and files[0].endswith(".bad")

    script(sandbox, "<html>captive portal</html>")
    with pytest.raises(open_meteo.OpenMeteoError, match="not JSON"):
        open_meteo.fetch_archive(40.0, -80.0, dt.date(2024, 3, 1), dt.date(2024, 3, 3))
    assert len(sandbox.calls) == 2

    script(sandbox, daily_body(dt.date(2024, 3, 1), 3))
    frame, _ = open_meteo.fetch_archive(40.0, -80.0, dt.date(2024, 3, 1), dt.date(2024, 3, 3))
    assert len(frame) == 3


def test_transient_failures_are_retried_and_4xx_is_not(
    sandbox: FakeRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped connection and a 5xx are retried; a 4xx is the request's fault and is not."""
    import requests

    from tests.test_footystats import FakeGet, FakeResponse

    monkeypatch.setattr(open_meteo, "_request", REAL_REQUEST)
    fake = FakeGet(
        requests.ConnectionError("reset"),
        FakeResponse(503, "busy"),
        FakeResponse(200, daily_body(dt.date(2024, 3, 1), 1)),
    )
    monkeypatch.setattr(requests, "get", fake)
    frame, _ = open_meteo.fetch_archive(40.0, -80.0, dt.date(2024, 3, 1), dt.date(2024, 3, 1))
    assert len(frame) == 1 and len(fake.calls) == 3
    assert all(c["url"] == open_meteo.ARCHIVE_URL for c in fake.calls)

    refused = FakeGet(FakeResponse(400, json.dumps({"error": True, "reason": "bad"})))
    monkeypatch.setattr(requests, "get", refused)
    with pytest.raises(open_meteo.OpenMeteoError, match="HTTP 400"):
        open_meteo.fetch_archive(40.0, -80.0, dt.date(2024, 4, 1), dt.date(2024, 4, 1))
    assert len(refused.calls) == 1
    assert not any(p.name.endswith(".bad") for p in config.ARCHIVE_DIR.iterdir())


def test_forecast_is_a_dated_snapshot_with_a_horizon(sandbox: FakeRequest) -> None:
    """Today's forecast and tomorrow's are different facts, archived apart."""
    script(sandbox, daily_body(TODAY, 4))
    frame, name = open_meteo.fetch_forecast(40.0, -80.0, days=4, today=TODAY)
    assert "_as_of_2024-03-20" in name
    assert list(frame["forecast_horizon_days"]) == [0, 1, 2, 3]
    assert set(frame["weather_source"]) == {"forecast"}
    _, params = sandbox.calls[0]
    assert params["forecast_days"] == 4

    script(sandbox, daily_body(TODAY + dt.timedelta(days=1), 4))
    _, tomorrow = open_meteo.fetch_forecast(40.0, -80.0, days=4, today=TODAY + dt.timedelta(days=1))
    assert tomorrow != name
    assert len(sandbox.calls) == 2


def test_end_before_start_is_refused(sandbox: FakeRequest) -> None:
    with pytest.raises(ValueError):
        open_meteo.fetch_archive(40.0, -80.0, dt.date(2024, 3, 3), dt.date(2024, 3, 1))
    assert sandbox.calls == []


# ---------------------------------------------------------------------------
# The refresh, through the real staging and mart
# ---------------------------------------------------------------------------


def _season(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame, **kw: Any
) -> None:
    stage_frames(con, tiny_season, tiny_clubs, **kw)


def distinct_grounds(clubs: pd.DataFrame) -> pd.DataFrame:
    """One stadium per club at its own coordinates, so no two clubs share a cached response."""
    return pd.DataFrame(
        [
            (club_id, f"{club_id} ground", 40.0 + i, -80.0 - i, "2000-01-01", "2099-12-31", "")
            for i, club_id in enumerate(sorted(clubs["club_id"].astype(str).unique()))
        ],
        columns=STADIUM_COLUMNS,
    )


def test_refresh_fetches_one_archive_range_per_club_and_joins_to_home_matches(
    sandbox: FakeRequest,
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """One request per club covering its home dates, every day written, mart joined on date.

    tiny_season: club_a is at home on 03-02 and 03-16, club_b on 03-09 and
    03-16, club_c on 03-02, club_d on 03-09. Four clubs, four requests, each
    spanning that club's first to last home date.
    """
    _season(con, tiny_season, tiny_clubs)
    # club_a 03-02..03-16 (15 days), club_b 03-09..03-16 (8), club_c 03-02 (1), club_d 03-09 (1)
    script(
        sandbox,
        daily_body(dt.date(2024, 3, 2), 15, base_temp=10),
        daily_body(dt.date(2024, 3, 9), 8, base_temp=20),
        daily_body(dt.date(2024, 3, 2), 1, base_temp=30),
        daily_body(dt.date(2024, 3, 9), 1, base_temp=40),
    )
    stats = refresh.refresh_played_matches(con, today=dt.date(2024, 4, 1))
    assert stats.archive_requests == 4
    assert stats.rows_archive == 15 + 8 + 1 + 1
    ranges = [(p["start_date"], p["end_date"]) for _, p in sandbox.calls]
    assert ranges == [
        ("2024-03-02", "2024-03-16"),
        ("2024-03-09", "2024-03-16"),
        ("2024-03-02", "2024-03-02"),
        ("2024-03-09", "2024-03-09"),
    ]

    runner.materialise(con, "stg_weather")
    for model in ("int_standings", "int_stakes", "mart_match_features"):
        runner.materialise(con, model)
    rows = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT match_id, weather_source, weather_horizon_days, temp_max_c, precipitation_mm "
            "FROM mart_match_features ORDER BY match_id"
        ).fetchall()
    }
    assert rows["m1"] == ("archive", None, 10.0, 1.5)  # club_a at home on 03-02, day 0 of its range
    assert rows["m5"] == ("archive", None, 24.0, 1.5)  # club_a at home on 03-16, day 14
    assert rows["m2"] == ("archive", None, 30.0, 1.5)  # club_c
    assert rows["m3"] == ("archive", None, 20.0, 1.5)  # club_b on 03-09, day 0
    assert rows["m6"] == ("archive", None, 27.0, 0.0)  # club_b on 03-16, day 7
    assert rows["m4"] == ("archive", None, 40.0, 1.5)  # club_d
    assert features_not_null(con).passed
    assert played_weather_is_observed(con).passed

    # nothing left to fetch: a second refresh makes no request
    again = refresh.refresh_played_matches(con, today=dt.date(2024, 4, 1))
    assert again.archive_requests == 0 and len(sandbox.calls) == 4


def test_forecast_covers_upcoming_fixtures_and_is_replaced_by_the_observation(
    sandbox: FakeRequest,
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """m5 and m6 (03-16) are unplayed on 03-12: forecast rows with horizon 4.

    After the matches are played, the refresh fetches the observation and the
    forecast row is upgraded - a played match never keeps forecast weather.
    """
    season = with_unplayed(tiny_season, ["m5", "m6"])
    grounds = distinct_grounds(tiny_clubs)
    _season(con, season, tiny_clubs, stadiums=grounds)
    on = dt.date(2024, 3, 12)
    # played matches first: club_a (03-02), club_b (03-09), club_c (03-02), club_d (03-09)
    # all within the archive lag of 03-12 minus 7 = 03-05? no: 03-09 is younger than the lag
    script(
        sandbox,
        daily_body(dt.date(2024, 3, 2), 1, base_temp=10),  # club_a 03-02
        daily_body(dt.date(2024, 3, 2), 1, base_temp=30),  # club_c 03-02
        daily_body(on, 16, base_temp=15),  # club_a forecast
        daily_body(on, 16, base_temp=25),  # club_b forecast
    )
    stats = refresh.refresh_played_matches(con, today=on)
    refresh.fetch_upcoming(con, today=on, stats=stats)
    assert stats.archive_requests == 2  # club_b and club_d played on 03-09, inside the lag
    assert stats.forecast_requests == 2
    assert stats.rows_forecast == 2  # only the fixture dates are written
    needs = refresh.weather_needs(con)
    assert len(needs) == 6
    assert int(needs["weather_source"].isna().sum()) == 2  # club_b, club_d on 03-09: not observable
    row = con.execute(
        "SELECT weather_source, forecast_horizon_days, temperature_2m_max FROM raw_weather "
        "WHERE club_id = 'club_a' AND date = DATE '2024-03-16'"
    ).fetchone()
    assert row == ("forecast", 4, 19.0)

    runner.materialise(con, "stg_weather")
    for model in ("int_standings", "int_stakes", "mart_match_features"):
        runner.materialise(con, model)
    mart = con.execute(
        "SELECT match_id, weather_source, weather_horizon_days FROM mart_match_features "
        "WHERE match_id IN ('m5', 'm6') ORDER BY match_id"
    ).fetchall()
    assert mart == [("m5", "forecast", 4), ("m6", "forecast", 4)]
    assert played_weather_is_observed(con).metadata["fixtures_forecast"] == 2

    # the matches are played; three weeks on, the archive has them
    # stage_frames recreates raw_weather, so put the forecast rows back to
    # simulate the state before the next weekly run
    _season(con, tiny_season, tiny_clubs, stadiums=grounds)
    con.execute(
        "INSERT INTO raw_weather VALUES ('club_a', DATE '2024-03-16', 40.0, -80.0, 'forecast', 4, "
        "19.0, 14.0, 0.0, 16.0, 50.0, TIMESTAMP '2024-03-12 06:00:00', 'snapshot'), "
        "('club_b', DATE '2024-03-16', 40.0, -80.0, 'forecast', 4, "
        "29.0, 24.0, 0.0, 16.0, 50.0, TIMESTAMP '2024-03-12 06:00:00', 'snapshot')"
    )
    later = dt.date(2024, 4, 8)
    script(
        sandbox,
        daily_body(dt.date(2024, 3, 2), 15, base_temp=10),  # club_a 03-02..03-16
        daily_body(dt.date(2024, 3, 9), 8, base_temp=20),  # club_b 03-09..03-16
        daily_body(dt.date(2024, 3, 2), 1, base_temp=30),  # club_c
        daily_body(dt.date(2024, 3, 9), 1, base_temp=40),  # club_d
    )
    stats = refresh.refresh_played_matches(con, today=later)
    refresh.fetch_upcoming(con, today=later, stats=stats)
    assert stats.rows_upgraded == 2
    assert stats.forecast_requests == 0  # nothing unplayed inside the horizon
    row = con.execute(
        "SELECT weather_source, forecast_horizon_days, temperature_2m_max FROM raw_weather "
        "WHERE club_id = 'club_a' AND date = DATE '2024-03-16'"
    ).fetchone()
    assert row == ("archive", None, 24.0)


def test_forecast_never_overwrites_an_observation(
    sandbox: FakeRequest,
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    season = with_unplayed(tiny_season, ["m5"])
    weather = pd.DataFrame(
        [("club_a", dt.date(2024, 3, 16), "archive", None, 24.0, 19.0, 0.0, 26.0, 50.0)],
        columns=[
            "club_id",
            "date",
            "weather_source",
            "forecast_horizon_days",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "wind_speed_10m_max",
            "cloud_cover_mean",
        ],
    )
    _season(con, season, tiny_clubs, weather=weather)
    stats = refresh.fetch_upcoming(con, today=dt.date(2024, 3, 12))
    assert stats.forecast_requests == 0 and sandbox.calls == []
    row = con.execute(
        "SELECT weather_source, temperature_2m_max FROM raw_weather WHERE club_id = 'club_a'"
    ).fetchone()
    assert row == ("archive", 24.0)


def test_stale_forecast_on_a_played_match_fails_the_mart_check(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Training data must not quietly contain predictions."""
    weather = pd.DataFrame(
        [("club_a", dt.date(2024, 3, 2), "forecast", 5, 12.0, 8.0, 0.0, 20.0, 60.0)],
        columns=[
            "club_id",
            "date",
            "weather_source",
            "forecast_horizon_days",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "wind_speed_10m_max",
            "cloud_cover_mean",
        ],
    )
    _season(con, tiny_season, tiny_clubs, weather=weather)
    for model in ("int_standings", "int_stakes", "mart_match_features"):
        runner.materialise(con, model)
    result = played_weather_is_observed(con)
    assert not result.passed
    assert result.metadata["stale_forecasts"] == [
        {"match_id": "m1", "club_id": "club_a", "date": "2024-03-02"}
    ]
    assert "usl.run weather" in result.metadata["hint"]


def test_disabled_weather_is_a_recorded_skip_not_a_fetch(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "WEATHER_ENABLED", False)
    fake = FakeRequest()
    monkeypatch.setattr(open_meteo, "_request", fake)
    _season(con, tiny_season, tiny_clubs)
    stats = refresh.refresh(con, today=TODAY)
    assert stats.skipped and fake.calls == []
    assert stats.as_metadata()["weather_skipped"] is True
    row = con.execute("SELECT count(*) FROM raw_weather").fetchone()
    assert row == (0,)


def test_refresh_covers_the_whole_example_season_and_the_mart_carries_it(
    sandbox: FakeRequest, con: duckdb.DuckDBPyConnection, example_archive_path: Path
) -> None:
    """The stage end to end on the real fixture list: one request per club and ground.

    Twenty clubs, twenty-one grounds (Tottenham moved from Wembley in April
    2019), every played home date observed, and the mart's weather columns
    filled on all 380 rows through the real SQL layer. A second refresh
    requests nothing.
    """
    import shutil

    from tests.test_standings import load_example_raw

    sandbox.auto = True
    # the sandbox archive is empty; the committed example season goes in beside
    # the weather (the fixture path was resolved after the sandbox moved the dir)
    committed = config.PROJECT_ROOT / "data" / "raw_archive" / example_archive_path.name
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived = config.ARCHIVE_DIR / example_archive_path.name
    shutil.copy(committed, archived)
    assert load_example_raw(con, archived) == 380
    stats = refresh.refresh(con, today=dt.date(2019, 6, 1))
    assert not stats.skipped
    assert stats.archive_requests == 21
    assert stats.forecast_requests == 0
    assert stats.club_days_needed == 380
    assert stats.club_days_missing == 0
    assert stats.no_stadium == 0
    spurs = [
        (p["latitude"], p["start_date"], p["end_date"])
        for _, p in sandbox.calls
        if p["latitude"] in (51.556, 51.6043)
    ]
    assert spurs == [
        (51.556, "2018-08-18", "2019-03-02"),
        (51.6043, "2019-04-03", "2019-05-12"),
    ]

    counts = runner.run_sql_layer(con)
    assert counts["stg_weather"] == stats.rows_archive
    observed = con.execute(
        "SELECT count(*) FILTER (WHERE weather_source = 'archive'), "
        "count(*) FILTER (WHERE temp_max_c IS NULL) FROM mart_match_features"
    ).fetchone()
    assert observed == (380, 0)
    assert played_weather_is_observed(con).metadata["played_observed"] == 380

    again = refresh.refresh(con, today=dt.date(2019, 6, 1))
    assert again.archive_requests == 0 and len(sandbox.calls) == 21


def test_reference_normaliser_keeps_coordinates_and_collapses_ids() -> None:
    """93.0 is the id 93; 27.94 is a latitude and must not become 27."""
    from usl.transform.reference import normalize_club_key

    assert normalize_club_key(93.0) == "93"
    assert normalize_club_key(93) == "93"
    assert normalize_club_key(27.94) == "27.94"
    assert normalize_club_key(-82.4512) == "-82.4512"


# ---------------------------------------------------------------------------
# Stadiums: validity ranges, exercise 12.1
# ---------------------------------------------------------------------------


def test_a_club_that_moved_grounds_gets_each_ground_for_its_own_dates(
    sandbox: FakeRequest,
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
) -> None:
    """club_a plays 03-02 at the old ground and 03-16 at the new one: two requests, two grounds."""
    stadiums = pd.DataFrame(
        [
            ("club_a", "Old Ground", 27.94, -82.45, "2017-01-01", "2024-03-10", ""),
            ("club_a", "New Ground", 27.96, -82.51, "2024-03-11", "2099-12-31", ""),
            ("club_b", "B", 40.0, -80.0, "2017-01-01", "2099-12-31", ""),
            ("club_c", "C", 40.0, -80.0, "2017-01-01", "2099-12-31", ""),
            ("club_d", "D", 40.0, -80.0, "2017-01-01", "2099-12-31", ""),
        ],
        columns=STADIUM_COLUMNS,
    )
    _season(con, tiny_season, tiny_clubs, stadiums=stadiums)
    assert home_matches_resolve_to_one_stadium(con).passed
    script(
        sandbox,
        daily_body(dt.date(2024, 3, 2), 1),  # club_a old ground
        daily_body(dt.date(2024, 3, 16), 1),  # club_a new ground
        daily_body(dt.date(2024, 3, 9), 8),  # club_b
        daily_body(dt.date(2024, 3, 2), 1),  # club_c
        daily_body(dt.date(2024, 3, 9), 1),  # club_d
    )
    refresh.refresh_played_matches(con, today=dt.date(2024, 4, 1))
    coords = [(p["latitude"], p["longitude"]) for _, p in sandbox.calls[:2]]
    assert coords == [(27.94, -82.45), (27.96, -82.51)]
    rows = con.execute(
        "SELECT date, lat FROM raw_weather WHERE club_id = 'club_a' ORDER BY date"
    ).fetchall()
    assert rows == [(dt.date(2024, 3, 2), 27.94), (dt.date(2024, 3, 16), 27.96)]


def test_overlapping_and_missing_stadium_ranges_are_named(
    con: duckdb.DuckDBPyConnection, tiny_season: pd.DataFrame, tiny_clubs: pd.DataFrame
) -> None:
    """A gap drops a match's weather and an overlap would duplicate it; both are silent in SQL."""
    stadiums = pd.DataFrame(
        [
            ("club_a", "Old", 27.94, -82.45, "2017-01-01", "2024-03-20", ""),
            ("club_a", "New", 27.96, -82.51, "2024-03-10", "2099-12-31", ""),  # overlaps 03-16
            ("club_b", "B", 40.0, -80.0, "2017-01-01", "2024-03-10", ""),  # gap after 03-10
            ("club_c", "C", 40.0, -80.0, "2017-01-01", "2099-12-31", ""),
            # club_d has no row at all
        ],
        columns=STADIUM_COLUMNS,
    )
    _season(con, tiny_season, tiny_clubs, stadiums=stadiums)
    result = home_matches_resolve_to_one_stadium(con)
    assert not result.passed
    assert result.metadata["overlapping"] == [
        {"club_id": "club_a", "date": "2024-03-16", "rows": 2}
    ]
    assert result.metadata["unresolved"] == [
        {"club_id": "club_b", "date": "2024-03-16"},
        {"club_id": "club_d", "date": "2024-03-09"},
    ]
    assert result.metadata["clubs_without_a_stadium"] == ["club_b", "club_d"]
    assert "stadiums.csv" in result.metadata["hint"]


def test_committed_stadiums_cover_every_club_season_in_the_conference_file(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The real CSVs: every club has a stadium row and no club has overlapping ranges."""
    from usl.transform.reference import read_reference_csv

    read_reference_csv(con, "club_conference", config.CLUB_CONFERENCE_CSV)
    read_reference_csv(con, "stadiums", config.STADIUMS_CSV)
    missing = con.execute(
        "SELECT DISTINCT c.club_id FROM club_conference c LEFT JOIN stadiums s USING (club_id) "
        "WHERE s.club_id IS NULL ORDER BY 1"
    ).fetchall()
    assert missing == []
    overlaps = con.execute(
        """
        SELECT a.club_id FROM stadiums a JOIN stadiums b
          ON a.club_id = b.club_id AND a.stadium < b.stadium
         AND CAST(a.valid_from AS DATE) <= CAST(b.valid_to AS DATE)
         AND CAST(b.valid_from AS DATE) <= CAST(a.valid_to AS DATE)
        """
    ).fetchall()
    assert overlaps == []
    bad_coords = con.execute(
        "SELECT club_id FROM stadiums WHERE TRY_CAST(lat AS DOUBLE) NOT BETWEEN 24 AND 56 "
        "OR TRY_CAST(lon AS DOUBLE) NOT BETWEEN -125 AND 2"
    ).fetchall()
    assert bad_coords == []


# ---------------------------------------------------------------------------
# The feature family
# ---------------------------------------------------------------------------


def test_weather_features_are_in_both_models_and_allowed_null() -> None:
    from usl.features.definitions import MODEL_FEATURES

    for name, features in MODEL_FEATURES.items():
        assert set(WEATHER_FEATURES) <= set(features), name
    assert set(WEATHER_FEATURES) <= config.ALLOWED_NULL_FEATURES
    assert "weather_source" in mart_columns() and "weather_horizon_days" in mart_columns()


def test_all_null_weather_is_dropped_before_training_and_named(
    con: duckdb.DuckDBPyConnection,
    tiny_season: pd.DataFrame,
    tiny_clubs: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no weather archived the columns are all null; the model must not see a constant."""
    from usl.models.train import train_all

    monkeypatch.setattr(config, "XGB_PARAMS", {**config.XGB_PARAMS, "n_estimators": 5})
    monkeypatch.setattr(config, "TEST_FRACTION", 0.34)
    # four seasons of the tiny fixture so the split has rows on both sides
    seasons = []
    for year in (2021, 2022, 2023, 2024):
        s = tiny_season.copy()
        s["season"] = year
        s["date"] = s["date"].str.replace("2024", str(year))
        s["match_id"] = s["match_id"] + f"_{year}"
        seasons.append(s)
    clubs = pd.concat(
        [tiny_clubs.assign(season=year) for year in (2021, 2022, 2023, 2024)], ignore_index=True
    )
    _season(con, pd.concat(seasons, ignore_index=True), clubs)
    for model in ("int_standings", "int_stakes", "mart_match_features", "mart_decay_curve"):
        runner.materialise(con, model)
    summary = train_all(con, dt.date(2024, 4, 1), seeds=[1])
    assert set(WEATHER_FEATURES) <= set(summary["all_null_features"])
    used = {r[0] for r in con.execute("SELECT DISTINCT feature FROM feature_importance").fetchall()}
    assert not (used & set(WEATHER_FEATURES))
