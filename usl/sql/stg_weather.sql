-- stg_weather: one row per club-day of weather at the club's ground.
--
-- Tier: staging
-- Doc:  docs/phases/12-phase-two-weather.md
--
-- Source: raw_weather, written by usl.weather.refresh from the archived
-- Open-Meteo responses. The renames here are the feature names the mart and
-- the models use; the units are Open-Meteo's (Celsius, millimetres,
-- kilometres per hour, percent). weather_source says whether a row is an
-- observation ('archive') or a prediction ('forecast'), and
-- forecast_horizon_days how far out the prediction was made.
--
-- The table exists on every run, empty until the weather backfill has been
-- archived, so the mart's LEFT JOIN is always well-formed and the weather
-- features are simply null until then.
--
-- Columns: club_id VARCHAR, date DATE, weather_source VARCHAR,
--          forecast_horizon_days INTEGER, temp_max_c DOUBLE, temp_min_c DOUBLE,
--          precipitation_mm DOUBLE, wind_max_kmh DOUBLE, cloud_cover_pct DOUBLE

SELECT
    CAST(club_id AS VARCHAR)                 AS club_id,
    CAST(date AS DATE)                       AS date,
    CAST(weather_source AS VARCHAR)          AS weather_source,
    CAST(forecast_horizon_days AS INTEGER)   AS forecast_horizon_days,
    CAST(temperature_2m_max AS DOUBLE)       AS temp_max_c,
    CAST(temperature_2m_min AS DOUBLE)       AS temp_min_c,
    CAST(precipitation_sum AS DOUBLE)        AS precipitation_mm,
    CAST(wind_speed_10m_max AS DOUBLE)       AS wind_max_kmh,
    CAST(cloud_cover_mean AS DOUBLE)         AS cloud_cover_pct
FROM raw_weather
ORDER BY club_id, date
