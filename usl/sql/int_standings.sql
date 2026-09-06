-- int_standings: conference table position as of each match date.
--
-- Tier: intermediate
-- Doc:  docs/phases/04-standings-as-of-match-date.md
--
-- The core of the pro-rel thesis. There is no table on the source for "the
-- standings on 14 June 2019", only the standings now, so it is reconstructed
-- as a running calculation over match history.
--
-- Grain: one row per club in the conference for every date on which any club
-- of that conference has a fixture, played or not (exercise 4.2, resolved in
-- favour of the full field), plus one snapshot row per conference-season the
-- day after its last fixture so the final table exists as a row.
--
-- POINT-IN-TIME. Running totals are computed INCLUDING each match
-- (ROWS ... AND CURRENT ROW, the *_after columns) and every grid row is then
-- joined to the club's most recent match STRICTLY before the grid date with an
-- ASOF join. For a club's own match date that is its previous match - exactly
-- ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING; for a date the club does
-- not play it is the carried-forward table, which the window form alone
-- cannot produce. checks.no_future_leakage recomputes pts_before by a
-- different method and compares, because a leak here does not raise.
--
-- TIE-BREAKING. Points, goal difference, goals for, with RANK() so genuinely
-- tied clubs share a position. Head-to-head is not implemented.
--
-- Conference is an attribute of the club-season and joins from stg_clubs on
-- (club_id, season), never off the match row - an interconference fixture has
-- no single correct value. The join is INNER on purpose: a club-season missing
-- from stg_clubs must not form a silent third conference of its own. It does
-- not vanish silently either, because checks.all_club_seasons_have_conference
-- runs on staging before this model is built.
--
-- Columns: season, conference, club_id, date, is_match_date, played_before,
--          pts_before, gd_before, gf_before, rank_before, n_clubs

WITH club_matches AS (
    -- unpivot played matches to one row per club per match
    SELECT
        season,
        date,
        home_club_id AS club_id,
        CASE WHEN home_goals > away_goals THEN 3
             WHEN home_goals = away_goals THEN 1
             ELSE 0 END AS points,
        home_goals   AS gf,
        away_goals   AS ga
    FROM stg_matches
    WHERE is_played
    UNION ALL
    SELECT
        season,
        date,
        away_club_id,
        CASE WHEN away_goals > home_goals THEN 3
             WHEN away_goals = home_goals THEN 1
             ELSE 0 END,
        away_goals,
        home_goals
    FROM stg_matches
    WHERE is_played
),
with_conference AS (
    SELECT m.season, c.conference, m.club_id, m.date, m.points, m.gf, m.ga
    FROM club_matches m
    JOIN stg_clubs c
      ON c.club_id = m.club_id AND c.season = m.season
),
running AS (
    -- cumulative totals INCLUDING the current match; the strict ASOF join
    -- below is what turns them into "before"
    SELECT
        season,
        conference,
        club_id,
        date,
        COUNT(*)      OVER w AS played_after,
        SUM(points)   OVER w AS pts_after,
        SUM(gf - ga)  OVER w AS gd_after,
        SUM(gf)       OVER w AS gf_after
    FROM with_conference
    WINDOW w AS (
        PARTITION BY season, club_id
        ORDER BY date
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )
),
fixtures AS (
    -- every (season, conference, club_id, date) on which a club has a fixture,
    -- played or not but never void, with the conference taken from the club-season
    SELECT m.season, c.conference, m.home_club_id AS club_id, m.date
    FROM stg_matches m
    JOIN stg_clubs c ON c.club_id = m.home_club_id AND c.season = m.season
    WHERE NOT m.is_void
    UNION
    SELECT m.season, c.conference, m.away_club_id, m.date
    FROM stg_matches m
    JOIN stg_clubs c ON c.club_id = m.away_club_id AND c.season = m.season
    WHERE NOT m.is_void
),
date_grid AS (
    SELECT DISTINCT season, conference, date FROM fixtures
    UNION
    -- the snapshot row: the day after the conference's last fixture
    SELECT season, conference, max(date) + 1 FROM fixtures GROUP BY season, conference
),
club_grid AS (
    -- the full field: every club of the conference-season on every grid date
    SELECT g.season, g.conference, g.date, c.club_id
    FROM date_grid g
    JOIN stg_clubs c ON c.season = g.season AND c.conference = g.conference
),
before AS (
    SELECT
        g.season,
        g.conference,
        g.club_id,
        g.date,
        COALESCE(r.played_after, 0) AS played_before,
        COALESCE(r.pts_after, 0)    AS pts_before,
        COALESCE(r.gd_after, 0)     AS gd_before,
        COALESCE(r.gf_after, 0)     AS gf_before
    FROM club_grid g
    ASOF LEFT JOIN running r
      ON g.club_id = r.club_id
     AND g.season = r.season
     AND g.date > r.date
)
SELECT
    b.season,
    b.conference,
    b.club_id,
    b.date,
    f.club_id IS NOT NULL                 AS is_match_date,
    CAST(b.played_before AS INTEGER)      AS played_before,
    CAST(b.pts_before AS INTEGER)         AS pts_before,
    CAST(b.gd_before AS INTEGER)          AS gd_before,
    CAST(b.gf_before AS INTEGER)          AS gf_before,
    CAST(RANK() OVER (
        PARTITION BY b.season, b.conference, b.date
        ORDER BY b.pts_before DESC, b.gd_before DESC, b.gf_before DESC
    ) AS INTEGER)                         AS rank_before,
    CAST(COUNT(*) OVER (
        PARTITION BY b.season, b.conference, b.date
    ) AS INTEGER)                         AS n_clubs
FROM before b
LEFT JOIN fixtures f
  ON f.season = b.season AND f.club_id = b.club_id AND f.date = b.date
ORDER BY b.season, b.conference, b.date, rank_before, b.club_id
