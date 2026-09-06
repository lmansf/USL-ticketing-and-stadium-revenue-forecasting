-- stg_matches: one row per match, typed, with canonical club ids.
--
-- Tier: staging
-- Doc:  docs/phases/03-club-name-consistency.md, docs/phases/05-sql-layer.md
--
-- Three jobs, and no others:
--   1. Type coercion. raw_matches holds the season, the score, and attendance
--      as VARCHAR because raw means raw. Goals and attendance are only kept on
--      played matches; an attendance of 0 or below is the source's way of
--      saying "unknown" and becomes NULL rather than a real gate of zero.
--   2. Canonical club ids, via a LEFT JOIN to club_aliases on the NORMALISED raw
--      string (whitespace collapsed, same rule as reference.normalize_club_key).
--      LEFT JOIN, not INNER - the inner join drops unmapped rows and tells you
--      nothing. checks.all_clubs_mapped turns the nulls into an error naming the
--      exact strings to add. No row is ever dropped in this file.
--   3. Derived calendar columns from the match DATE, which is the kick-off
--      taken in ref_config.match_tz (config.MATCH_TZ). dayofweek() is
--      0 = Sunday .. 6 = Saturday.
--
-- NOT here: standings, lags, or anything requiring a window over other
-- matches. Those belong in int_standings and mart_match_features.
--
-- Columns: match_id, season, season_id, date, kickoff_utc, status, is_played,
--          home_raw, away_raw, home_club_id, away_club_id, home_goals,
--          away_goals, attendance, is_covid_affected, day_of_week, month,
--          is_weekend, is_midweek

WITH typed AS (
    SELECT
        r.match_id,
        -- '2018/2019' -> 2018: the season is named by its starting year.
        -- TRY_CAST so a malformed value keeps its row (as a NULL season the
        -- conference check then names) instead of aborting the whole model.
        TRY_CAST(substr(r.season_raw, 1, 4) AS INTEGER)                     AS season,
        r.season_id,
        (to_timestamp(r.date_unix) AT TIME ZONE (SELECT match_tz FROM ref_config))::DATE
                                                                            AS date,
        to_timestamp(r.date_unix) AT TIME ZONE 'UTC'                        AS kickoff_utc,
        r.status,
        r.home_raw,
        r.away_raw,
        TRY_CAST(r.home_goals AS INTEGER)                                   AS home_goals_raw,
        TRY_CAST(r.away_goals AS INTEGER)                                   AS away_goals_raw,
        TRY_CAST(r.attendance AS INTEGER)                                   AS attendance_raw
    FROM raw_matches r
),
played AS (
    SELECT
        t.*,
        COALESCE(
            t.status = 'complete'
            AND t.home_goals_raw IS NOT NULL
            AND t.away_goals_raw IS NOT NULL,
            FALSE
        ) AS is_played
    FROM typed t
)
SELECT
    p.match_id,
    p.season,
    p.season_id,
    p.date,
    p.kickoff_utc,
    p.status,
    p.is_played,
    p.home_raw,
    p.away_raw,
    h.club_id                                                               AS home_club_id,
    a.club_id                                                               AS away_club_id,
    CASE WHEN p.is_played THEN p.home_goals_raw END                         AS home_goals,
    CASE WHEN p.is_played THEN p.away_goals_raw END                         AS away_goals,
    CASE WHEN p.is_played AND p.attendance_raw > 0 THEN p.attendance_raw END AS attendance,
    COALESCE(
        p.date BETWEEN (SELECT covid_start FROM ref_config)
                   AND (SELECT covid_end FROM ref_config),
        FALSE
    )                                                                       AS is_covid_affected,
    CAST(dayofweek(p.date) AS INTEGER)                                      AS day_of_week,
    CAST(month(p.date) AS INTEGER)                                          AS month,
    dayofweek(p.date) IN (0, 6)                                             AS is_weekend,
    dayofweek(p.date) IN (2, 3, 4)                                          AS is_midweek
FROM played p
-- The alias side is already normalised by reference.read_reference_csv; the
-- raw side is normalised here with the same expression (reference.NORMALIZE_SQL).
LEFT JOIN club_aliases h
    ON trim(regexp_replace(CAST(p.home_raw AS VARCHAR), '\s+', ' ', 'g')) = h.raw_name
LEFT JOIN club_aliases a
    ON trim(regexp_replace(CAST(p.away_raw AS VARCHAR), '\s+', ' ', 'g')) = a.raw_name
ORDER BY p.date, p.match_id
