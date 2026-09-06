"""Open-Meteo client: observed weather from the archive endpoint, predicted from the forecast one.

Free for non-commercial use, no API key, historical archive going back decades,
and a forecast endpoint for upcoming fixtures. You pass latitude, longitude,
and a date range - which drops the stadium-to-station mapping problem entirely.

Every response goes through the same archive as the FootyStats responses
(usl.ingest.archive): served from data/raw_archive/ when present, otherwise
fetched, written to a .partial file, validated, and committed in one rename.
Historical weather never changes, so an archive response is fetched once;
a forecast changes every day, so it is archived as a dated snapshot and the
row it produces carries its horizon.

See docs/phases/12-phase-two-weather.md
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import requests

from usl import config
from usl.ingest import archive

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Archive endpoint names: they become the filename prefix under data/raw_archive/.
ARCHIVE_ENDPOINT = "open-meteo-archive"
FORECAST_ENDPOINT = "open-meteo-forecast"

DAILY_FIELDS: tuple[str, ...] = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "cloud_cover_mean",
)

SOURCE_ARCHIVE = "archive"
SOURCE_FORECAST = "forecast"

# Coordinates are rounded before they become part of a filename or a request,
# so 51.55490000001 and 51.5549 are the same cached response.
_COORD_DECIMALS = 4

# Replaceable in tests so no test touches the network.
_sleep: Callable[[float], None] = time.sleep


class OpenMeteoError(RuntimeError):
    """A request failed, the body was not JSON, or the API reported an error."""


def _request(url: str, params: dict[str, Any]) -> str:
    """GET one URL, retrying transient failures only.

    Connection errors, timeouts and 5xx are retried with the FootyStats backoff
    settings; a 4xx is an error in the request and is raised at once. Nothing
    secret is in play here (Open-Meteo has no key), so the URL may be logged.

    Args:
        url: The endpoint.
        params: Query parameters.

    Returns:
        The response body.

    Raises:
        OpenMeteoError: A non-transient failure, or the attempts ran out.
    """
    attempts = max(1, config.FETCH_MAX_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ContentDecodingError,
        ) as exc:
            reason = type(exc).__name__
        except requests.RequestException as exc:
            raise OpenMeteoError(f"{url}: {type(exc).__name__}; not retried") from None
        else:
            status = int(response.status_code)
            if status < 400:
                log.info(
                    "%s: HTTP %s, %d bytes (attempt %d/%d)",
                    url,
                    status,
                    len(response.text.encode("utf-8")),
                    attempt,
                    attempts,
                )
                return response.text
            if status < 500:
                raise OpenMeteoError(
                    f"{url}: HTTP {status} for {params.get('latitude')},{params.get('longitude')} "
                    f"{params.get('start_date', '')}..{params.get('end_date', '')}; not retried"
                )
            reason = f"HTTP {status}"
        if attempt == attempts:
            raise OpenMeteoError(
                f"{url} failed after {attempts} attempt(s); last failure: {reason}"
            )
        delay = config.FETCH_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
        log.warning(
            "%s: %s - attempt %d/%d, retrying in %.0fs", url, reason, attempt, attempts, delay
        )
        _sleep(delay)
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


def _coord(value: float) -> float:
    return round(float(value), _COORD_DECIMALS)


def _validate(payload: Any) -> str | None:
    """Why a parsed body is not a usable Open-Meteo daily response, or None."""
    if not isinstance(payload, dict):
        return "the body is not a JSON object"
    if payload.get("error"):
        return f"the API reported an error: {payload.get('reason', '')!r}"
    daily = payload.get("daily")
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        return "the body has no daily.time series"
    return None


def get(
    endpoint: str,
    url: str,
    key_params: dict[str, Any],
    request_params: dict[str, Any],
    *,
    force: bool = False,
    snapshot: str | dt.date | None = None,
) -> tuple[dict[str, Any], str]:
    """Fetch one Open-Meteo response, serving from the archive when possible.

    Args:
        endpoint: Archive endpoint name (ARCHIVE_ENDPOINT or FORECAST_ENDPOINT).
        url: The URL to request on a miss.
        key_params: The parameters that identify the response in the archive.
            Not the field list: it is fixed by DAILY_FIELDS.
        request_params: The full query string for the API.
        force: Re-request even when archived.
        snapshot: A pull date, for a forecast: each day's forecast is its own
            archive entry rather than a hit on yesterday's.

    Returns:
        The parsed body and the archive file name it lives in.

    Raises:
        OpenMeteoError: The request failed, the body was not JSON, or the API
            reported an error. In the last two cases the body is kept as
            '.bad' and the archive is unchanged.
    """
    tag = archive.snapshot_tag(snapshot) if snapshot is not None else None
    path = archive.archive_path(endpoint, key_params, tag=tag)
    if not force and archive.is_archived(endpoint, key_params, tag=tag):
        log.info("archive hit %s", path.name)
        return archive.read_archived(endpoint, key_params, tag=tag), path.name

    log.info("archive miss %s - requesting %s", path.name, endpoint)
    body = _request(url, request_params)
    partial = archive.write_partial(endpoint, key_params, body, tag=tag)
    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError as exc:
        bad = archive.quarantine_partial(partial)
        raise OpenMeteoError(
            f"{endpoint} {key_params}: the response is not JSON ({exc.msg} at char {exc.pos}). "
            f"The body is kept at {bad.name}; the archive was not changed."
        ) from None
    problem = _validate(payload)
    if problem is not None:
        bad = archive.quarantine_partial(partial)
        raise OpenMeteoError(
            f"{endpoint} {key_params}: {problem}. The body is kept at {bad.name}; "
            "the archive was not changed."
        )
    final = archive.commit_partial(partial)
    return payload, final.name


def daily_frame(
    payload: dict[str, Any], *, source: str, today: dt.date | None = None
) -> pd.DataFrame:
    """One row per day from a validated response.

    Args:
        payload: A body that passed validation.
        source: SOURCE_ARCHIVE or SOURCE_FORECAST.
        today: For a forecast, the date the forecast was made; the horizon is
            measured from it. Ignored for an archive response.

    Returns:
        Columns: date, DAILY_FIELDS, weather_source, forecast_horizon_days.
    """
    daily = payload["daily"]
    dates = [dt.date.fromisoformat(str(d)) for d in daily["time"]]
    frame = pd.DataFrame({"date": dates})
    for field in DAILY_FIELDS:
        values = daily.get(field)
        frame[field] = pd.array(
            [None if v is None else float(v) for v in values]
            if isinstance(values, list) and len(values) == len(dates)
            else [None] * len(dates),
            dtype="Float64",
        )
    frame["weather_source"] = source
    if source == SOURCE_FORECAST:
        made_on = today or dt.date.today()
        frame["forecast_horizon_days"] = pd.array(
            [(d - made_on).days for d in dates], dtype="Int64"
        )
    else:
        frame["forecast_horizon_days"] = pd.array([None] * len(dates), dtype="Int64")
    return frame


def fetch_archive(
    lat: float, lon: float, start: dt.date, end: dt.date, *, force: bool = False
) -> tuple[pd.DataFrame, str]:
    """Fetch observed daily weather for one location over a date range.

    One call per club covering its full date range, not one call per match -
    that would be thousands of requests for data you can get in a few dozen.
    The response is archived, and historical weather never changes, so the
    same range is never requested twice.

    Args:
        lat: Latitude.
        lon: Longitude.
        start: First date, inclusive.
        end: Last date, inclusive.
        force: Re-request even when archived.

    Returns:
        The daily frame (weather_source = 'archive') and the archive file name.

    Raises:
        ValueError: end is before start.
        OpenMeteoError: See get().
    """
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    key = {
        "latitude": _coord(lat),
        "longitude": _coord(lon),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    params = {**key, "daily": ",".join(DAILY_FIELDS), "timezone": "UTC"}
    payload, name = get(ARCHIVE_ENDPOINT, ARCHIVE_URL, key, params, force=force)
    return daily_frame(payload, source=SOURCE_ARCHIVE), name


def fetch_forecast(
    lat: float,
    lon: float,
    days: int | None = None,
    *,
    today: dt.date | None = None,
    force: bool = False,
) -> tuple[pd.DataFrame, str]:
    """Fetch forecast daily weather for one location.

    Archived as a dated snapshot, one per day the forecast is made, because
    tomorrow's forecast for Saturday is a different fact from today's. Each
    row records its horizon: a fixture ten days out has a materially worse
    weather input than one three days out, and a model fed forecasts at
    prediction time after training on observations has a distribution shift
    baked in. The horizon column is what lets that be measured.

    Args:
        lat: Latitude.
        lon: Longitude.
        days: Forecast horizon. Defaults to config.WEATHER_FORECAST_DAYS.
        today: The date the forecast is made. Defaults to today.
        force: Re-request even when today's snapshot is archived.

    Returns:
        The daily frame (weather_source = 'forecast', with
        forecast_horizon_days) and the archive file name.

    Raises:
        OpenMeteoError: See get().
    """
    made_on = today or dt.date.today()
    horizon = int(days if days is not None else config.WEATHER_FORECAST_DAYS)
    key = {"latitude": _coord(lat), "longitude": _coord(lon), "forecast_days": horizon}
    params = {**key, "daily": ",".join(DAILY_FIELDS), "timezone": "UTC"}
    payload, name = get(FORECAST_ENDPOINT, FORECAST_URL, key, params, force=force, snapshot=made_on)
    return daily_frame(payload, source=SOURCE_FORECAST, today=made_on), name
