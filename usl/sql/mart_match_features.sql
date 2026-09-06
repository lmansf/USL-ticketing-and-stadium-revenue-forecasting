-- mart_match_features: one row per match, model-ready.
--
-- Tier: mart
-- Doc:  docs/phases/06-features.md
--
-- The only table the models read. Both of them - the split between baseline
-- and prorel happens in code by column selection, not here. Columns are
-- EXACTLY usl.features.definitions.mart_columns(), in that order;
-- tests/test_features.py enforces both directions.
--
-- One row per stg_matches row that is not void. Home matches only is
-- automatic - every match has exactly one home club - and unplayed fixtures
-- are kept with attendance NULL and is_played = false, because forecasts for
-- remaining home matches need their features. A void fixture (cancelled,
-- never to be played) is left out: it is not a match and gets no forecast.
-- Training filters on is_played.
--
-- CALENDAR AND LAG
--   The lag history is the club's PLAYED, NON-COVID home matches with a known
--   gate, ordered by date and crossing season boundaries (support level
--   carries over; the alternative leaves every opener with null lags). COVID
--   is handled before the lags, not after, so a 2021 moving average is not
--   dragged toward empty-stadium figures. The windows include the current
--   row (*_after) and are then joined to EVERY match by a strict ASOF join on
--   date, so each match sees only gates strictly before its own date. For a
--   played non-COVID match that is identical to ROWS BETWEEN 3 PRECEDING AND
--   1 PRECEDING; it also gives unplayed and COVID fixtures their features.
--   same_fixture_last_season is the gate the last time this pairing was
--   played at this ground in the previous season, same exclusions.
--
-- MATCH CONTEXT
--   is_derby from usl/ref/derbies.csv, either direction. matches_remaining
--   from int_stakes, which counts the schedule rather than hardcoding it.
--
-- WEATHER (phase two)
--   From stg_weather on (home_club_id, date): observed for a played match once
--   the archive has it, forecast for a coming fixture, null otherwise. Shared
--   by both models. weather_source and weather_horizon_days ride along as
--   non-feature columns so the drill-down can say which kind a row got.
--
-- PRO-REL
--   rank_before and the stakes columns are the home club's int_standings and
--   int_stakes rows on the match date. opponent_rank_before is the away club's
--   rank in ITS OWN conference. matches_since_elimination is -1 while live (a
--   sentinel, never NULL) and otherwise the count of the club's earlier home
--   fixtures in the season since the elimination date, so the first home match
--   after elimination is 0. points_from_relegation_line is instrumented, not
--   validated: no relegation exists in USL data.

WITH gates AS (
    -- the lag history, windows INCLUDING the current row
    SELECT
        home_club_id,
        date,
        attendance AS gate_after,
        AVG(attendance) OVER (
            PARTITION BY home_club_id ORDER BY date, match_id
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) AS ma3_after,
        AVG(attendance) OVER (
            PARTITION BY home_club_id ORDER BY date, match_id
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS ma5_after
    FROM stg_matches
    WHERE is_played AND NOT is_covid_affected AND attendance IS NOT NULL
),
lagged AS (
    -- strictly earlier gates only: m.date > g.date
    SELECT
        m.match_id,
        g.gate_after AS last_home_gate,
        g.ma3_after  AS home_gate_ma3,
        g.ma5_after  AS home_gate_ma5
    FROM stg_matches m
    ASOF LEFT JOIN gates g
      ON g.home_club_id = m.home_club_id
     AND m.date > g.date
),
previous_fixture AS (
    -- the most recent time this (home, away) pairing was played at this
    -- ground in the season before, when a gate was recorded
    SELECT
        m.match_id,
        p.attendance AS same_fixture_last_season,
        ROW_NUMBER() OVER (PARTITION BY m.match_id ORDER BY p.date DESC, p.match_id DESC) AS rn
    FROM stg_matches m
    JOIN stg_matches p
      ON p.home_club_id = m.home_club_id
     AND p.away_club_id = m.away_club_id
     AND p.season = m.season - 1
     AND p.is_played
     AND NOT p.is_covid_affected
     AND p.attendance IS NOT NULL
),
home_sequence AS (
    SELECT
        match_id,
        ROW_NUMBER() OVER (
            PARTITION BY home_club_id, season ORDER BY date, match_id
        ) = 1 AS is_season_opener,
        ROW_NUMBER() OVER (
            PARTITION BY home_club_id, season ORDER BY date DESC, match_id DESC
        ) = 1 AS is_final_home_match
    FROM stg_matches
    WHERE NOT is_void
),
derby_pairs AS (
    -- both directions, deduplicated, so the join below cannot fan out even if
    -- the CSV lists a pair twice
    SELECT club_id_a AS home_id, club_id_b AS away_id FROM derbies
    UNION
    SELECT club_id_b, club_id_a FROM derbies
),
joined AS (
    SELECT
        m.match_id,
        m.season,
        m.date,
        m.home_club_id,
        m.attendance,
        m.is_played,
        m.is_covid_affected,
        m.day_of_week,
        m.month,
        m.is_weekend,
        m.is_midweek,
        l.last_home_gate,
        l.home_gate_ma3,
        l.home_gate_ma5,
        pf.same_fixture_last_season,
        m.away_club_id AS opponent_club_id,
        dp.home_id IS NOT NULL AS is_derby,
        kh.matches_remaining,
        hs.is_season_opener,
        hs.is_final_home_match,
        sh.rank_before,
        sa.rank_before AS opponent_rank_before,
        kh.points_from_playoff_line,
        kh.is_mathematically_live,
        kh.points_from_relegation_line,
        -- live on this date: never eliminated, or eliminated later
        (kh.eliminated_on IS NULL OR m.date < kh.eliminated_on) AS home_is_live,
        w.weather_source,
        w.forecast_horizon_days AS weather_horizon_days,
        w.temp_max_c,
        w.temp_min_c,
        w.precipitation_mm,
        w.wind_max_kmh,
        w.cloud_cover_pct
    FROM stg_matches m
    LEFT JOIN lagged l
      ON l.match_id = m.match_id
    LEFT JOIN previous_fixture pf
      ON pf.match_id = m.match_id AND pf.rn = 1
    LEFT JOIN home_sequence hs
      ON hs.match_id = m.match_id
    LEFT JOIN derby_pairs dp
      ON dp.home_id = m.home_club_id AND dp.away_id = m.away_club_id
    LEFT JOIN int_standings sh
      ON sh.club_id = m.home_club_id AND sh.season = m.season AND sh.date = m.date
    LEFT JOIN int_standings sa
      ON sa.club_id = m.away_club_id AND sa.season = m.season AND sa.date = m.date
    LEFT JOIN int_stakes kh
      ON kh.club_id = m.home_club_id AND kh.season = m.season AND kh.date = m.date
    LEFT JOIN stg_weather w
      ON w.club_id = m.home_club_id AND w.date = m.date
    -- a void fixture is not a match: no features, no forecast
    WHERE NOT m.is_void
)
SELECT
    j.match_id,
    j.season,
    j.date,
    j.home_club_id,
    j.attendance,
    j.is_played,
    j.is_covid_affected,
    j.weather_source,
    j.weather_horizon_days,
    -- calendar and lag
    CAST(j.day_of_week AS INTEGER)                      AS day_of_week,
    CAST(j.month AS INTEGER)                            AS month,
    j.is_weekend,
    j.is_midweek,
    CAST(j.last_home_gate AS INTEGER)                   AS last_home_gate,
    CAST(j.home_gate_ma3 AS DOUBLE)                     AS home_gate_ma3,
    CAST(j.home_gate_ma5 AS DOUBLE)                     AS home_gate_ma5,
    CAST(j.same_fixture_last_season AS INTEGER)         AS same_fixture_last_season,
    -- weather (phase two)
    j.temp_max_c,
    j.temp_min_c,
    j.precipitation_mm,
    j.wind_max_kmh,
    j.cloud_cover_pct,
    -- match context
    j.opponent_club_id,
    j.is_derby,
    j.matches_remaining,
    j.is_season_opener,
    j.is_final_home_match,
    -- pro-rel
    j.rank_before,
    j.opponent_rank_before,
    CAST(j.opponent_rank_before - j.rank_before AS INTEGER) AS rank_gap,
    j.points_from_playoff_line,
    j.is_mathematically_live,
    CAST(CASE
        WHEN j.home_is_live THEN -1
        -- elimination is absorbing, so "earlier home fixtures on or after the
        -- elimination date" is "earlier home fixtures where the club was not live"
        ELSE COUNT(*) FILTER (WHERE NOT j.home_is_live) OVER (
            PARTITION BY j.home_club_id, j.season
            ORDER BY j.date, j.match_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        )
    END AS INTEGER)                                     AS matches_since_elimination,
    j.points_from_relegation_line
FROM joined j
ORDER BY j.date, j.match_id
