-- mart_decay_curve: the dead-rubber decay curve as a table.
--
-- Tier: mart
-- Doc:  docs/phases/06-features.md (exercise 6.3)
--
-- Mean attendance on eliminated-club home matches by matches_since_elimination,
-- indexed against each club-season's OWN baseline: the mean gate of its played,
-- non-COVID home matches before elimination (matches_since_elimination = -1).
-- Comparing a club only to itself removes the biggest confounder - eliminated
-- clubs are bad clubs and draw smaller crowds all season - and most of the
-- population drift along the tail. Club-seasons with no pre-elimination home
-- gate have no baseline and are excluded.
--
-- n is reported with every point on purpose: the tail thins out fast, and a
-- point resting on four matches should look like it rests on four matches.
--
-- Columns: matches_since_elimination INTEGER, n INTEGER,
--          index_vs_own_baseline DOUBLE, mean_attendance DOUBLE,
--          n_club_seasons INTEGER

WITH home_gates AS (
    SELECT season, home_club_id, attendance, matches_since_elimination
    FROM mart_match_features
    WHERE is_played AND NOT is_covid_affected AND attendance IS NOT NULL
),
baseline AS (
    SELECT season, home_club_id, AVG(attendance) AS baseline_gate
    FROM home_gates
    WHERE matches_since_elimination = -1
    GROUP BY season, home_club_id
)
SELECT
    h.matches_since_elimination,
    CAST(COUNT(*) AS INTEGER)                            AS n,
    AVG(h.attendance / b.baseline_gate)                  AS index_vs_own_baseline,
    AVG(h.attendance)                                    AS mean_attendance,
    CAST(COUNT(DISTINCT (h.season, h.home_club_id)) AS INTEGER) AS n_club_seasons
FROM home_gates h
JOIN baseline b
  ON b.season = h.season AND b.home_club_id = h.home_club_id
WHERE h.matches_since_elimination >= 0
GROUP BY h.matches_since_elimination
ORDER BY h.matches_since_elimination
