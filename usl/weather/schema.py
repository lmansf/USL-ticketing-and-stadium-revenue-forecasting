"""The raw_weather table.

Kept apart from the refresh so the SQL runner can create the table without
importing the client: stg_weather must exist even when no weather has ever
been fetched, so the mart's LEFT JOIN has something to join to.

See docs/phases/12-phase-two-weather.md
"""

from __future__ import annotations

import duckdb

# One row per club-day. Typed at write time, unlike raw_matches, because
# Open-Meteo returns numbers with declared units and there is no text to keep.
# weather_source is 'archive' (observed) or 'forecast' (predicted), and
# forecast_horizon_days is the distance the forecast was made at - null for an
# observation. source_file names the archived response the row came from.
RAW_WEATHER_DDL = """
CREATE TABLE IF NOT EXISTS raw_weather (
    club_id                VARCHAR   NOT NULL,
    date                   DATE      NOT NULL,
    lat                    DOUBLE,
    lon                    DOUBLE,
    weather_source         VARCHAR   NOT NULL,
    forecast_horizon_days  INTEGER,
    temperature_2m_max     DOUBLE,
    temperature_2m_min     DOUBLE,
    precipitation_sum      DOUBLE,
    wind_speed_10m_max     DOUBLE,
    cloud_cover_mean       DOUBLE,
    fetched_at             TIMESTAMP,
    source_file            VARCHAR,
    PRIMARY KEY (club_id, date)
)
"""


def ensure_weather_table(con: duckdb.DuckDBPyConnection) -> None:
    """Create raw_weather if it is missing. Idempotent.

    Args:
        con: Open connection with write access.
    """
    con.execute(RAW_WEATHER_DDL)
