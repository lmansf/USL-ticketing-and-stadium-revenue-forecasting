"""Open-Meteo client. PHASE TWO - deferred.

Free for non-commercial use, no API key, historical archive going back decades,
and a forecast endpoint for upcoming fixtures. You pass latitude, longitude, and
a date range - which drops the stadium-to-station mapping problem entirely.

Nothing here is required for the phase-one deliverable.

See docs/phases/12-phase-two-weather.md
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

DAILY_FIELDS: tuple[str, ...] = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "cloud_cover_mean",
)


def fetch_archive(lat: float, lon: float, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Fetch observed daily weather for one location over a date range.

    One call per club covering its full date range, not one call per match -
    that would be thousands of requests for data you can get in a few dozen.

    Cache the responses. Historical weather never changes, so re-requesting it on
    every run is pure waste.

    Args:
        lat: Latitude.
        lon: Longitude.
        start: First date, inclusive.
        end: Last date, inclusive.

    Returns:
        One row per day with DAILY_FIELDS, plus weather_source = 'archive'.

    TODO: phase two.
    """
    raise NotImplementedError("PHASE TWO - see docs/phases/12-phase-two-weather.md")


def fetch_forecast(lat: float, lon: float, days: int = 16) -> pd.DataFrame:
    """Fetch forecast daily weather for one location.

    Record the horizon. Forecast accuracy degrades with distance, so a fixture
    ten days out has a materially worse weather input than one three days out,
    and a model trained on archive data and fed forecast data has a distribution
    shift baked in. A weather_source column plus the horizon in days costs
    nothing and lets you answer "is the model worse on long-horizon fixtures".

    Args:
        lat: Latitude.
        lon: Longitude.
        days: Forecast horizon.

    Returns:
        One row per day with DAILY_FIELDS, plus weather_source = 'forecast' and
        forecast_horizon_days.

    TODO: phase two.
    """
    raise NotImplementedError("PHASE TWO - see docs/phases/12-phase-two-weather.md")


def refresh_played_matches(con: object) -> int:
    """Replace forecast weather with archive weather for matches now played.

    A match played three days ago whose weather was fetched as a forecast a week
    earlier must be re-fetched from the archive, overwriting the forecast value.
    Otherwise the training data quietly contains forecasts.

    Args:
        con: Open DuckDB connection with write access.

    Returns:
        Number of rows upgraded from forecast to archive.

    TODO: phase two.
    """
    raise NotImplementedError("PHASE TWO - see docs/phases/12-phase-two-weather.md")
