-- int_stakes: the playoff-line and relegation-line arithmetic, per standings row.
--
-- Tier: intermediate
-- Doc:  docs/phases/06-features.md (exercise 6.2), docs/reference/build-decisions.md
--
-- Kept out of the mart so the stakes numbers can be read on their own, one row
-- per int_standings row. The mart picks up the home club's row on the match
-- date.
--
-- The playoff line is the current points of the club in the last qualifying
-- position, and the relegation line the current points of the club in the last
-- SAFE position (n_clubs - relegation_spots). Positions here use ROW_NUMBER
-- over the same ordering as rank_before, with club_id as a final tiebreak so
-- the position is deterministic; tied clubs have equal points so the line
-- value is the same whichever of them sits in the slot.
--
-- is_mathematically_live compares the most a club can still reach
-- (pts_before + 3 * matches_remaining) against the line club's CURRENT points,
-- strictly greater: in the worst case the line club loses everything, and a
-- tie goes to tie-breakers this model has not done. It is an approximation
-- (no head-to-head constraint solving) and it errs towards keeping clubs live,
-- which is the conservative direction for the decay curve. Once false it stays
-- false: the reachable total never rises and the line never falls.
--
-- One artefact of "strictly greater" to know about: a club with no matches
-- left reads not-live even when it holds the last qualifying place itself
-- (pts + 0 > pts is false). On the snapshot row the day after the season that
-- is every club except those clear of the line, and on the real season it
-- marks the club that finished fourth as eliminated on the snapshot date.
-- The snapshot is not a match date, so no mart row and no decay-curve point
-- is affected, and a club whose fixtures are done has no home match left for
-- the flag to describe. Left as is rather than special-cased.
--
-- Spots come from conference_structure (usl/ref/conference_structure.csv) by
-- (season, conference), falling back to ref_config.default_playoff_spots and
-- ref_config.assumed_relegation_spots. A season with no row and no default
-- gets NULL stakes, and checks.features_not_null stops the run naming the
-- column. Do not invent a number here.
--
-- Columns: season, conference, club_id, date, fixtures_total,
--          matches_remaining, playoff_spots, relegation_spots,
--          playoff_line_pts, relegation_line_pts, points_from_playoff_line,
--          points_from_relegation_line, is_mathematically_live, eliminated_on

WITH fixture_counts AS (
    -- the club's scheduled fixtures in the season, played or not, from the
    -- schedule rather than a hardcoded number - it is not constant across
    -- seasons. A void fixture (cancelled, never to be played) is not one:
    -- counting it would keep an eliminated club reading live by three points
    SELECT season, club_id, COUNT(*) AS fixtures_total
    FROM (
        SELECT season, home_club_id AS club_id FROM stg_matches WHERE NOT is_void
        UNION ALL
        SELECT season, away_club_id FROM stg_matches WHERE NOT is_void
    )
    GROUP BY season, club_id
),
structure AS (
    SELECT
        CAST(season AS INTEGER)            AS season,
        conference,
        TRY_CAST(playoff_spots AS INTEGER)    AS playoff_spots,
        TRY_CAST(relegation_spots AS INTEGER) AS relegation_spots
    FROM conference_structure
),
base AS (
    SELECT
        s.season,
        s.conference,
        s.club_id,
        s.date,
        s.pts_before,
        s.played_before,
        s.n_clubs,
        COALESCE(fc.fixtures_total, 0)                            AS fixtures_total,
        COALESCE(st.playoff_spots, cfg.default_playoff_spots)     AS playoff_spots,
        COALESCE(st.relegation_spots, cfg.assumed_relegation_spots) AS relegation_spots,
        ROW_NUMBER() OVER (
            PARTITION BY s.season, s.conference, s.date
            ORDER BY s.pts_before DESC, s.gd_before DESC, s.gf_before DESC, s.club_id
        )                                                         AS position
    FROM int_standings s
    LEFT JOIN fixture_counts fc
      ON fc.season = s.season AND fc.club_id = s.club_id
    LEFT JOIN structure st
      ON st.season = s.season AND st.conference = s.conference
    CROSS JOIN ref_config cfg
),
lines AS (
    SELECT
        b.*,
        b.fixtures_total - b.played_before AS matches_remaining,
        MAX(CASE WHEN b.position = b.playoff_spots THEN b.pts_before END) OVER (
            PARTITION BY b.season, b.conference, b.date
        )                                  AS playoff_line_pts,
        MAX(CASE WHEN b.position = b.n_clubs - b.relegation_spots THEN b.pts_before END) OVER (
            PARTITION BY b.season, b.conference, b.date
        )                                  AS relegation_line_pts
    FROM base b
),
live AS (
    SELECT
        l.*,
        (l.pts_before + 3 * l.matches_remaining) > l.playoff_line_pts AS is_mathematically_live
    FROM lines l
),
elimination AS (
    SELECT
        v.*,
        -- the first grid date on which the club was not live; NULL while live
        MIN(CASE WHEN NOT v.is_mathematically_live THEN v.date END) OVER (
            PARTITION BY v.season, v.club_id
        )                                  AS first_not_live
    FROM live v
)
SELECT
    e.season,
    e.conference,
    e.club_id,
    e.date,
    CAST(e.fixtures_total AS INTEGER)                        AS fixtures_total,
    CAST(e.matches_remaining AS INTEGER)                     AS matches_remaining,
    CAST(e.playoff_spots AS INTEGER)                         AS playoff_spots,
    CAST(e.relegation_spots AS INTEGER)                      AS relegation_spots,
    CAST(e.playoff_line_pts AS INTEGER)                      AS playoff_line_pts,
    CAST(e.relegation_line_pts AS INTEGER)                   AS relegation_line_pts,
    CAST(e.playoff_line_pts - e.pts_before AS INTEGER)       AS points_from_playoff_line,
    CAST(e.relegation_line_pts - e.pts_before AS INTEGER)    AS points_from_relegation_line,
    e.is_mathematically_live,
    CASE WHEN e.is_mathematically_live THEN NULL
         WHEN e.date >= e.first_not_live THEN e.first_not_live END AS eliminated_on
FROM elimination e
ORDER BY e.season, e.conference, e.date, e.position
