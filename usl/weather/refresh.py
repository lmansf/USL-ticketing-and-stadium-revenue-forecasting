"""Keep raw_weather current: observations for played matches, forecasts for coming ones.

What needs weather is decided from staging: every non-void home fixture of a
mapped club, joined to usl/ref/stadiums.csv on the club and the validity range
that covers the match date, which is where a club that moved grounds gets the
right coordinates for its older matches (exercise 12.1). The staging tables
are rebuilt here first, cheaply, so the needs reflect what ingest just landed.

Two rules keep observations and predictions apart:

- An observation overwrites a forecast; a forecast never overwrites an
  observation. A match played three days ago whose weather was fetched as a
  forecast a week earlier is re-fetched from the archive on the next run,
  otherwise the training data quietly contains forecasts.
- Every row says which it is (weather_source) and, for a forecast, how far
  out it was made (forecast_horizon_days).

Requests are grouped: one archive call per club and ground covering the whole
range of dates still missing, and one forecast call per club and ground for
the fixtures inside the horizon. Nothing is requested twice - the responses
are archived under data/raw_archive/.

See docs/phases/12-phase-two-weather.md
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, cast

import duckdb
import pandas as pd

from usl import config
from usl.logging_setup import utcnow
from usl.transform import runner
from usl.weather import open_meteo
from usl.weather.schema import ensure_weather_table

log = logging.getLogger(__name__)


@dataclass
class WeatherStats:
    """What one refresh did, for the run log.

    Attributes:
        skipped: config.WEATHER_ENABLED was off; nothing was fetched.
        archive_requests: Archive responses read, from disk or the network.
        forecast_requests: Forecast responses read, likewise.
        rows_archive: Club-days written from observations.
        rows_forecast: Club-days written from forecasts.
        rows_upgraded: Rows that were forecasts and are now observations.
        club_days_needed: Home fixtures that want a weather row.
        club_days_missing: Of those, the ones still without one afterwards.
        no_stadium: Home fixtures whose club and date match no stadiums.csv row.
        files: Archive file names touched.
    """

    skipped: bool = False
    archive_requests: int = 0
    forecast_requests: int = 0
    rows_archive: int = 0
    rows_forecast: int = 0
    rows_upgraded: int = 0
    club_days_needed: int = 0
    club_days_missing: int = 0
    no_stadium: int = 0
    files: list[str] = field(default_factory=list)

    def as_metadata(self) -> dict[str, Any]:
        """The run-log view: every count, plus the file list length."""
        return {
            "weather_skipped": self.skipped,
            "weather_archive_requests": self.archive_requests,
            "weather_forecast_requests": self.forecast_requests,
            "weather_rows_archive": self.rows_archive,
            "weather_rows_forecast": self.rows_forecast,
            "weather_rows_upgraded": self.rows_upgraded,
            "weather_club_days_needed": self.club_days_needed,
            "weather_club_days_missing": self.club_days_missing,
            "weather_no_stadium": self.no_stadium,
            "weather_files": len(self.files),
        }


_NEEDS_SQL = """
WITH home AS (
    SELECT m.home_club_id AS club_id, m.date, m.is_played
    FROM stg_matches m
    WHERE NOT m.is_void AND m.home_club_id IS NOT NULL
)
SELECT
    h.club_id,
    h.date,
    h.is_played,
    TRY_CAST(s.lat AS DOUBLE) AS lat,
    TRY_CAST(s.lon AS DOUBLE) AS lon,
    s.stadium,
    w.weather_source
FROM home h
LEFT JOIN stadiums s
  ON s.club_id = h.club_id
 AND h.date BETWEEN TRY_CAST(s.valid_from AS DATE) AND TRY_CAST(s.valid_to AS DATE)
LEFT JOIN raw_weather w
  ON w.club_id = h.club_id AND w.date = h.date
ORDER BY h.club_id, h.date
"""


def weather_needs(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Every home fixture with its ground's coordinates and its current weather row.

    Args:
        con: Open connection; stg_matches, stadiums and raw_weather must exist.

    Returns:
        Columns: club_id, date, is_played, lat, lon, stadium, weather_source.
        lat and lon are null when no stadiums.csv row covers the date; a
        check names those, this function only counts them.
    """
    frame = con.execute(_NEEDS_SQL).df()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame


def _upsert(
    con: duckdb.DuckDBPyConnection,
    club_id: str,
    lat: float,
    lon: float,
    rows: pd.DataFrame,
    *,
    source_file: str,
    overwrite_forecast: bool,
) -> tuple[int, int]:
    """Write daily rows for one club, honouring the observation-over-forecast rule.

    Args:
        con: Open connection with write access.
        club_id: The club.
        lat: Latitude the rows were fetched for.
        lon: Longitude.
        rows: A daily frame from open_meteo.
        source_file: Archive file name recorded on each row.
        overwrite_forecast: True for observations, which replace a forecast
            row; False for forecasts, which never replace anything.

    Returns:
        Rows written, and of those the forecast rows replaced by observations.
    """
    if rows.empty:
        return 0, 0
    frame = rows.copy()
    frame.insert(0, "club_id", club_id)
    frame["lat"] = float(lat)
    frame["lon"] = float(lon)
    frame["fetched_at"] = utcnow()
    frame["source_file"] = source_file
    con.register("_weather_rows", frame)
    if overwrite_forecast:
        upgraded_row = con.execute(
            """
            SELECT count(*) FROM raw_weather w
            JOIN _weather_rows r ON r.club_id = w.club_id AND r.date = w.date
            WHERE w.weather_source = 'forecast'
            """
        ).fetchone()
        upgraded = int(upgraded_row[0]) if upgraded_row else 0
        con.execute(
            """
            INSERT INTO raw_weather
            SELECT club_id, date, lat, lon, weather_source, forecast_horizon_days,
                   temperature_2m_max, temperature_2m_min, precipitation_sum,
                   wind_speed_10m_max, cloud_cover_mean, fetched_at, source_file
            FROM _weather_rows
            ON CONFLICT (club_id, date) DO UPDATE SET
                lat = excluded.lat,
                lon = excluded.lon,
                weather_source = excluded.weather_source,
                forecast_horizon_days = excluded.forecast_horizon_days,
                temperature_2m_max = excluded.temperature_2m_max,
                temperature_2m_min = excluded.temperature_2m_min,
                precipitation_sum = excluded.precipitation_sum,
                wind_speed_10m_max = excluded.wind_speed_10m_max,
                cloud_cover_mean = excluded.cloud_cover_mean,
                fetched_at = excluded.fetched_at,
                source_file = excluded.source_file
            """
        )
        written = int(len(frame))
    else:
        upgraded = 0
        # a forecast row is written only where there is no observation
        written_row = con.execute(
            """
            SELECT count(*) FROM _weather_rows r
            LEFT JOIN raw_weather w ON w.club_id = r.club_id AND w.date = r.date
            WHERE w.club_id IS NULL OR w.weather_source = 'forecast'
            """
        ).fetchone()
        written = int(written_row[0]) if written_row else 0
        con.execute(
            """
            INSERT INTO raw_weather
            SELECT r.club_id, r.date, r.lat, r.lon, r.weather_source, r.forecast_horizon_days,
                   r.temperature_2m_max, r.temperature_2m_min, r.precipitation_sum,
                   r.wind_speed_10m_max, r.cloud_cover_mean, r.fetched_at, r.source_file
            FROM _weather_rows r
            LEFT JOIN raw_weather w ON w.club_id = r.club_id AND w.date = r.date
            WHERE w.club_id IS NULL OR w.weather_source = 'forecast'
            ON CONFLICT (club_id, date) DO UPDATE SET
                lat = excluded.lat,
                lon = excluded.lon,
                weather_source = excluded.weather_source,
                forecast_horizon_days = excluded.forecast_horizon_days,
                temperature_2m_max = excluded.temperature_2m_max,
                temperature_2m_min = excluded.temperature_2m_min,
                precipitation_sum = excluded.precipitation_sum,
                wind_speed_10m_max = excluded.wind_speed_10m_max,
                cloud_cover_mean = excluded.cloud_cover_mean,
                fetched_at = excluded.fetched_at,
                source_file = excluded.source_file
            """
        )
    con.unregister("_weather_rows")
    return written, upgraded


def _groups(frame: pd.DataFrame) -> list[tuple[str, float, float, pd.DataFrame]]:
    """Split needs by (club, ground), dropping fixtures with no ground."""
    located = frame[frame["lat"].notna() & frame["lon"].notna()]
    out = []
    for key, rows in located.groupby(["club_id", "lat", "lon"], sort=True):
        club_id, lat, lon = cast(tuple[str, float, float], key)
        out.append((str(club_id), float(lat), float(lon), rows))
    return out


def refresh_played_matches(
    con: duckdb.DuckDBPyConnection,
    *,
    today: dt.date | None = None,
    force: bool = False,
    stats: WeatherStats | None = None,
) -> WeatherStats:
    """Replace missing or forecast weather with observations for played matches.

    One archive request per club and ground, covering the earliest to the
    latest played home date still without an observation, clipped to what the
    archive can have (today minus config.WEATHER_ARCHIVE_LAG_DAYS). Every day
    of the response is written, so a rescheduled fixture in the same range
    already has its row.

    Args:
        con: Open connection with write access; staging must be built.
        today: The date to measure the archive lag from. Defaults to today.
        force: Re-request archived responses.
        stats: Accumulate into an existing WeatherStats.

    Returns:
        The stats.
    """
    on = today or dt.date.today()
    stats = stats or WeatherStats()
    ensure_weather_table(con)
    needs = weather_needs(con)
    stats.no_stadium += int(needs["lat"].isna().sum())
    latest_observable = on - dt.timedelta(days=config.WEATHER_ARCHIVE_LAG_DAYS)
    wanted = needs[
        needs["is_played"].astype(bool)
        & (needs["weather_source"] != open_meteo.SOURCE_ARCHIVE)
        & (needs["date"] <= latest_observable)
    ]
    for club_id, lat, lon, rows in _groups(wanted):
        start = min(rows["date"])
        end = min(max(rows["date"]), latest_observable)
        frame, name = open_meteo.fetch_archive(lat, lon, start, end, force=force)
        stats.archive_requests += 1
        stats.files.append(name)
        written, upgraded = _upsert(
            con, club_id, lat, lon, frame, source_file=name, overwrite_forecast=True
        )
        stats.rows_archive += written
        stats.rows_upgraded += upgraded
        log.info(
            "weather archive %s %s..%s: %d day(s) written, %d forecast row(s) replaced (%s)",
            club_id,
            start,
            end,
            written,
            upgraded,
            name,
        )
    return stats


def fetch_upcoming(
    con: duckdb.DuckDBPyConnection,
    *,
    today: dt.date | None = None,
    force: bool = False,
    stats: WeatherStats | None = None,
) -> WeatherStats:
    """Forecast weather for the unplayed home fixtures inside the horizon.

    One forecast request per club and ground, archived as today's snapshot,
    and only the fixture dates are written - never over an observation.

    Args:
        con: Open connection with write access; staging must be built.
        today: The date the forecast is made. Defaults to today.
        force: Re-request today's snapshot.
        stats: Accumulate into an existing WeatherStats.

    Returns:
        The stats.
    """
    on = today or dt.date.today()
    stats = stats or WeatherStats()
    ensure_weather_table(con)
    needs = weather_needs(con)
    horizon_end = on + dt.timedelta(days=config.WEATHER_FORECAST_DAYS - 1)
    wanted = needs[
        ~needs["is_played"].astype(bool)
        & (needs["weather_source"] != open_meteo.SOURCE_ARCHIVE)
        & (needs["date"] >= on)
        & (needs["date"] <= horizon_end)
    ]
    for club_id, lat, lon, rows in _groups(wanted):
        frame, name = open_meteo.fetch_forecast(lat, lon, today=on, force=force)
        stats.forecast_requests += 1
        stats.files.append(name)
        fixture_days = frame[frame["date"].isin(set(rows["date"]))]
        written, _ = _upsert(
            con, club_id, lat, lon, fixture_days, source_file=name, overwrite_forecast=False
        )
        stats.rows_forecast += written
        log.info("weather forecast %s: %d fixture day(s) written from %s", club_id, written, name)
    return stats


def refresh(
    con: duckdb.DuckDBPyConnection, *, today: dt.date | None = None, force: bool = False
) -> WeatherStats:
    """The weather stage: rebuild staging, then observations, then forecasts.

    With config.WEATHER_ENABLED off the table is created and nothing is
    fetched; the stats say so, and the run log records the stage as skipped
    rather than as a refresh that found nothing.

    Args:
        con: Open connection with write access; raw_matches must be loaded.
        today: The reference date. Defaults to today.
        force: Re-request archived responses.

    Returns:
        The stats.
    """
    ensure_weather_table(con)
    if not config.WEATHER_ENABLED:
        log.info(
            "weather: disabled (USL_WEATHER_ENABLED is not set) - the weather features stay null"
        )
        return WeatherStats(skipped=True)
    on = today or dt.date.today()
    runner.load_reference_tables(con)
    runner.materialise(con, "stg_clubs")
    runner.materialise(con, "stg_matches")
    stats = WeatherStats()
    refresh_played_matches(con, today=on, force=force, stats=stats)
    fetch_upcoming(con, today=on, force=force, stats=stats)
    needs = weather_needs(con)
    # no_stadium was counted once per pass above; report it once
    stats.no_stadium = int(needs["lat"].isna().sum())
    stats.club_days_needed = int(len(needs))
    stats.club_days_missing = int(needs["weather_source"].isna().sum())
    log.info(
        "weather: %d home fixture(s), %d without a weather row afterwards, %d without a "
        "stadium row; %d archive and %d forecast response(s) read",
        stats.club_days_needed,
        stats.club_days_missing,
        stats.no_stadium,
        stats.archive_requests,
        stats.forecast_requests,
    )
    return stats
