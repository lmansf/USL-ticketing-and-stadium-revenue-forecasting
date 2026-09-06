-- stg_clubs: one row per club-season, with conference and display name.
--
-- Tier: staging
-- Doc:  docs/phases/03-club-name-consistency.md
--
-- Source: club_conference (usl/ref/club_conference.csv), read with every
-- column as VARCHAR. Typing happens here.
--
-- Conference membership is NOT a constant. Clubs have moved between
-- conferences across seasons and the number of conferences has itself changed,
-- so the grain is the club-season. Do not read the current season's
-- conferences and apply them backwards - that is wrong for every club that
-- moved, and it is wrong silently.
--
-- The display name lives on the same row for the same reason: the API's name
-- for a club is its CURRENT name, and a 2017 match should not render under a
-- 2026 brand.
--
-- Columns: club_id VARCHAR, season INTEGER, conference VARCHAR,
--          display_name VARCHAR

SELECT
    club_id,
    CAST(season AS INTEGER) AS season,
    conference,
    display_name
FROM club_conference
ORDER BY season, conference, club_id
