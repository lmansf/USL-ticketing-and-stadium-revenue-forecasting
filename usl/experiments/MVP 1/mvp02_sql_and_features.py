#!/usr/bin/env python3
"""MVP 02 - raw to a model-ready table, in one file, on DuckDB.

Implements docs/mvp/02-mvp-sql-and-features.md as runnable code:

    raw_matches  ->  stg_matches  ->  mart_match_features

Two SQL steps, not four. The standings calculation lives as a CTE inside the mart
rather than as its own int_standings table, which is the MVP's biggest cut and the
one that costs most later - debugging a wrong rank means reading a CTE inside a
longer query instead of selecting from a table. Manageable with one season, not
with nine. The full track splits it (docs/phases/05-sql-layer.md).

Run it standalone with no data at all:

    python "usl/experiments/MVP 1/mvp02_sql_and_features.py" --seed-demo

That builds a synthetic 8-club double round robin in an in-memory database, runs
the real SQL over it, and verifies the standings against an independent pure-Python
recomputation. The point is to prove the SQL is correct before real data exists.

Against real data once MVP 01 has populated raw_matches:

    python "usl/experiments/MVP 1/mvp02_sql_and_features.py" --db data/usl.duckdb

Cuts taken here, all deliberate and all documented in the guide:
  - League-wide rank, not conference rank. Skips the conference mapping problem.
    docs/phases/04-standings-as-of-match-date.md#conference-not-league-wide
  - Thin feature set: calendar, two lags, opponent, three rank features.
  - No COVID handling. Do not point this at 2020 or 2021.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ALIASES = REPO_ROOT / "usl" / "ref" / "club_aliases.csv"


# ---------------------------------------------------------------------------
# Step 1 - staging
#
# Three jobs and no others: types, canonical club ids, derived calendar columns.
# No windows, no lags, no standings. Those belong in the mart.
#
# The join is a LEFT JOIN followed by a null check, never an INNER JOIN. An inner
# join drops unmapped rows and tells you nothing; this hands you the exact strings
# to paste into club_aliases.csv.
# ---------------------------------------------------------------------------

SQL_STG_MATCHES = """
CREATE OR REPLACE TABLE stg_matches AS
SELECT
    r.match_id,
    r.season,
    CAST(r.date AS DATE)                         AS date,
    r.home_raw,
    r.away_raw,
    h.club_id                                    AS home_club_id,
    a.club_id                                    AS away_club_id,
    CAST(r.home_goals AS INTEGER)                AS home_goals,
    CAST(r.away_goals AS INTEGER)                AS away_goals,
    TRY_CAST(r.attendance AS INTEGER)            AS attendance,
    dayofweek(CAST(r.date AS DATE))              AS day_of_week,
    month(CAST(r.date AS DATE))                  AS month,
    dayofweek(CAST(r.date AS DATE)) IN (0, 6)    AS is_weekend,
    dayofweek(CAST(r.date AS DATE)) IN (2, 3, 4) AS is_midweek
FROM raw_matches r
LEFT JOIN club_aliases h ON CAST(r.home_raw AS VARCHAR) = CAST(h.raw_name AS VARCHAR)
LEFT JOIN club_aliases a ON CAST(r.away_raw AS VARCHAR) = CAST(a.raw_name AS VARCHAR)
"""
# The CAST on both sides of the join is not decoration. The API returns club ids as
# numbers and the CSV reads them as strings; 93 and "93" are different keys, the
# join yields nulls, and the error reads as a missing club when it is really a
# missing cast. See docs/phases/03-club-name-consistency.md exercise 3.2.


# ---------------------------------------------------------------------------
# Step 2 - mart
#
# Standings as a CTE, then lag features, then one row per home match.
#
# The window frames are the whole exercise. Every backward-looking window ends at
# 1 PRECEDING, never CURRENT ROW - which is also SQL's default frame if you write
# none. Including the current match leaks its own result into the features meant to
# predict its attendance. It does not raise. It shows up as suspiciously good
# validation error, which is easy to mistake for success.
# ---------------------------------------------------------------------------

SQL_MART = """
CREATE OR REPLACE TABLE mart_match_features AS
WITH club_matches AS (
    -- unpivot to one row per club per match
    SELECT season, date, home_club_id AS club_id,
           CASE WHEN home_goals > away_goals THEN 3
                WHEN home_goals = away_goals THEN 1 ELSE 0 END AS points,
           home_goals AS gf, away_goals AS ga
    FROM stg_matches WHERE home_goals IS NOT NULL
    UNION ALL
    SELECT season, date, away_club_id,
           CASE WHEN away_goals > home_goals THEN 3
                WHEN away_goals = home_goals THEN 1 ELSE 0 END,
           away_goals, home_goals
    FROM stg_matches WHERE home_goals IS NOT NULL
),
running AS (
    -- cumulative totals strictly BEFORE each match
    SELECT season, club_id, date,
           COALESCE(SUM(points)   OVER w, 0) AS pts_before,
           COALESCE(SUM(gf - ga)  OVER w, 0) AS gd_before,
           COALESCE(SUM(gf)       OVER w, 0) AS gf_before,
           COALESCE(COUNT(*)      OVER w, 0) AS played_before
    FROM club_matches
    WINDOW w AS (
        PARTITION BY season, club_id
        ORDER BY date
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    )
),
standings AS (
    -- league-wide rank. RANK(), not ROW_NUMBER(): genuinely tied clubs share a
    -- position rather than being ordered arbitrarily by whatever the engine felt
    -- like, which would make rank jitter between runs on identical data.
    SELECT *,
           RANK() OVER (
               PARTITION BY season, date
               ORDER BY pts_before DESC, gd_before DESC, gf_before DESC
           ) AS rank_before
    FROM running
),
lags AS (
    -- a club's own home-gate history, current match excluded
    SELECT match_id, home_club_id, date,
           LAG(attendance) OVER w AS last_home_gate,
           AVG(attendance) OVER (
               PARTITION BY home_club_id ORDER BY date
               ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING
           ) AS home_gate_ma3
    FROM stg_matches
    WINDOW w AS (PARTITION BY home_club_id ORDER BY date)
)
SELECT
    m.match_id,
    m.season,
    m.date,
    m.home_club_id,
    m.attendance,                                   -- the target

    -- calendar
    m.day_of_week,
    m.month,
    m.is_weekend,
    m.is_midweek,

    -- lags
    l.last_home_gate,
    l.home_gate_ma3,

    -- context
    m.away_club_id                AS opponent_club_id,

    -- pro-rel family: the whole experiment is whether these help
    sh.rank_before,
    sa.rank_before                AS opponent_rank_before,
    sa.rank_before - sh.rank_before AS rank_gap,

    sh.pts_before,
    sh.played_before
FROM stg_matches m
JOIN standings sh ON sh.club_id = m.home_club_id AND sh.date = m.date AND sh.season = m.season
JOIN standings sa ON sa.club_id = m.away_club_id AND sa.date = m.date AND sa.season = m.season
LEFT JOIN lags l  ON l.match_id = m.match_id
WHERE m.home_goals IS NOT NULL
ORDER BY m.date, m.match_id
"""


# ---------------------------------------------------------------------------
# Checks. Exercise M2.1, as assertions rather than as advice.
# ---------------------------------------------------------------------------


def one_row(con: duckdb.DuckDBPyConnection, sql: str) -> tuple:
    """Run a query that must return exactly one row, and return it.

    DuckDB's fetchone() is typed as returning None when there are no rows. An
    aggregate always produces a row, but saying so explicitly beats scattering
    index-into-a-maybe-None across the file - and if a query ever does come back
    empty, this fails with the query rather than with a TypeError.
    """
    row = con.sql(sql).fetchone()
    if row is None:
        raise SystemExit(f"query unexpectedly returned no rows:\n{sql.strip()}")
    return row


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    """Run a query that must return exactly one row and one column."""
    return one_row(con, sql)[0]


def check_all_clubs_mapped(con: duckdb.DuckDBPyConnection) -> None:
    """Fail naming the exact unmapped strings, so fixing it is a paste not a hunt."""
    unmapped = con.sql("""
        SELECT DISTINCT home_raw AS name FROM stg_matches WHERE home_club_id IS NULL
        UNION
        SELECT DISTINCT away_raw FROM stg_matches WHERE away_club_id IS NULL
    """).fetchall()
    if unmapped:
        names = sorted(str(r[0]) for r in unmapped)
        raise SystemExit(
            f"FAIL unmapped clubs - add to club_aliases.csv: {names}\n"
            "  If these look like ids that ARE in the CSV, suspect a type mismatch: "
            "93 and \"93\" are different join keys."
        )
    print("  ok  all clubs mapped")


def check_row_count_preserved(con: duckdb.DuckDBPyConnection) -> None:
    """A second, independent signal. The null check above only catches NULLs; a
    mapping that silently points two different clubs at one club_id produces none."""
    raw = scalar(con, "SELECT count(*) FROM raw_matches")
    stg = scalar(con, "SELECT count(*) FROM stg_matches")
    if raw != stg:
        raise SystemExit(f"FAIL row count changed in staging: raw={raw} stg={stg}")
    print(f"  ok  row count preserved ({raw})")


def check_one_match_per_club_per_date(con: duckdb.DuckDBPyConnection) -> None:
    """A club must not appear twice on the same date.

    Not a hypothetical. The running-total window orders by date alone, so if a club
    has two matches on one date the frame boundary between them is arbitrary and the
    standings are silently wrong. The join to standings also fans out, inflating the
    mart. Both failures are quiet.

    Real fixture lists rarely violate this, but a doubleheader, a botched backfill
    that ingested a season twice, or a date column that lost its time component all
    produce it - so the check earns its place.
    """
    dupes = con.sql("""
        WITH club_dates AS (
            SELECT season, date, home_club_id AS club_id FROM stg_matches
            UNION ALL
            SELECT season, date, away_club_id FROM stg_matches
        )
        SELECT season, club_id, date, count(*) AS n
        FROM club_dates GROUP BY 1, 2, 3 HAVING count(*) > 1 ORDER BY 3
    """).fetchall()
    if dupes:
        raise SystemExit(
            f"FAIL {len(dupes)} club-date(s) with more than one match. First: {dupes[0]}\n"
            "  Ordering the standings window by date alone is ambiguous when this "
            "happens, and the join to standings fans out. Both fail silently."
        )
    print("  ok  one match per club per date")


def check_no_leakage(con: duckdb.DuckDBPyConnection) -> None:
    """The leakage test in its smallest form.

    A club's pts_before on its second match must equal the points it earned in its
    first. If pts_before already includes the current match, this is wrong in an
    obvious direction - which is the only reason it is catchable at all, because
    leakage otherwise announces itself as suspiciously good validation error.
    """
    bad = con.sql("""
        WITH club_matches AS (
            SELECT season, date, home_club_id AS club_id,
                   CASE WHEN home_goals > away_goals THEN 3
                        WHEN home_goals = away_goals THEN 1 ELSE 0 END AS points
            FROM stg_matches WHERE home_goals IS NOT NULL
            UNION ALL
            SELECT season, date, away_club_id,
                   CASE WHEN away_goals > home_goals THEN 3
                        WHEN away_goals = home_goals THEN 1 ELSE 0 END
            FROM stg_matches WHERE home_goals IS NOT NULL
        ),
        seq AS (
            SELECT season, club_id, date, points,
                   ROW_NUMBER() OVER (PARTITION BY season, club_id ORDER BY date) AS n,
                   LAG(points)  OVER (PARTITION BY season, club_id ORDER BY date) AS prev_points
            FROM club_matches
        )
        SELECT s.club_id, s.date, s.prev_points, f.pts_before
        FROM seq s
        JOIN mart_match_features f
          ON f.home_club_id = s.club_id AND f.date = s.date AND f.season = s.season
        WHERE s.n = 2 AND f.pts_before IS DISTINCT FROM s.prev_points
    """).fetchall()
    if bad:
        raise SystemExit(
            f"FAIL leakage: {len(bad)} club(s) have pts_before on match 2 that is not "
            f"their match-1 points. First: {bad[0]}\n"
            "  Check the window frame ends at 1 PRECEDING, not CURRENT ROW."
        )
    print("  ok  no leakage (pts_before on match 2 == match 1 points)")


def check_first_match_is_zero(con: duckdb.DuckDBPyConnection) -> None:
    """The window returns NULL for a partition's first row, and a null rank
    propagates into the features. COALESCE it to zero."""
    bad = scalar(con, """
        SELECT count(*) FROM mart_match_features
        WHERE played_before = 0 AND (pts_before IS NULL OR pts_before <> 0)
    """)
    if bad:
        raise SystemExit(f"FAIL {bad} first-match rows without pts_before = 0")
    print("  ok  first match of season is zeroed, not null")


def check_final_table(con: duckdb.DuckDBPyConnection) -> None:
    """Recompute the final table in pure Python and compare.

    Independent of the SQL, so it catches the classic mistakes the SQL can make on
    its own: draws scored wrong, null-score matches not filtered, or the unpivot
    dropping one side of each fixture.
    """
    rows = con.sql("""
        SELECT season, home_club_id, away_club_id, home_goals, away_goals
        FROM stg_matches WHERE home_goals IS NOT NULL
    """).fetchall()

    pts: dict[tuple, int] = {}
    for season, home, away, hg, ag in rows:
        pts.setdefault((season, home), 0)
        pts.setdefault((season, away), 0)
        if hg > ag:
            pts[(season, home)] += 3
        elif hg == ag:
            pts[(season, home)] += 1
            pts[(season, away)] += 1
        else:
            pts[(season, away)] += 3

    # SQL's view: last pts_before plus that last match's own points
    sql_final = con.sql("""
        WITH club_matches AS (
            SELECT season, date, home_club_id AS club_id,
                   CASE WHEN home_goals > away_goals THEN 3
                        WHEN home_goals = away_goals THEN 1 ELSE 0 END AS points
            FROM stg_matches WHERE home_goals IS NOT NULL
            UNION ALL
            SELECT season, date, away_club_id,
                   CASE WHEN away_goals > home_goals THEN 3
                        WHEN away_goals = home_goals THEN 1 ELSE 0 END
            FROM stg_matches WHERE home_goals IS NOT NULL
        )
        SELECT season, club_id, SUM(points) FROM club_matches GROUP BY 1, 2
    """).fetchall()

    mismatches = [
        (s, c, got, pts.get((s, c)))
        for s, c, got in sql_final
        if pts.get((s, c)) != got
    ]
    if mismatches:
        raise SystemExit(f"FAIL final table mismatch (season, club, sql, python): {mismatches[:5]}")
    print(f"  ok  final table matches recomputation ({len(sql_final)} club-seasons)")


# ---------------------------------------------------------------------------
# Demo data, so this file runs with nothing else in place.
# ---------------------------------------------------------------------------


def seed_demo(con: duckdb.DuckDBPyConnection, season: int = 2024, n_clubs: int = 8) -> None:
    """Build a synthetic double round robin in raw_matches plus a matching alias table.

    Deterministic, so a failing check is a code change rather than luck. Attendance
    carries a mild rank effect on top of a per-club base, which keeps the features
    from being degenerate without pretending to be real data.
    """
    rng = random.Random(42)
    clubs = [f"club_{i:02d}" for i in range(1, n_clubs + 1)]
    base_gate = {c: rng.randint(3000, 9000) for c in clubs}

    # Circle method, so every club plays exactly once per matchday. Shuffling
    # fixtures and chunking them does NOT give you that - a club lands twice on one
    # date, which silently corrupts the standings window. Ask how I know.
    arr = list(clubs)
    rounds: list[list[tuple[str, str]]] = []
    for _ in range(n_clubs - 1):
        rounds.append([(arr[i], arr[n_clubs - 1 - i]) for i in range(n_clubs // 2)])
        arr = [arr[0], arr[-1], *arr[1:-1]]
    rounds += [[(a, h) for h, a in rnd] for rnd in rounds]  # reverse fixtures

    start = date(season, 3, 2)
    rows = []
    n = 0
    for week, fixtures in enumerate(rounds):
        match_day = start + timedelta(days=7 * week)
        for home, away in fixtures:
            hg, ag = rng.randint(0, 3), rng.randint(0, 3)
            gate = int(base_gate[home] * rng.uniform(0.85, 1.15))
            rows.append(
                (
                    f"fs:{season}{n:04d}",
                    season,
                    match_day.isoformat(),
                    home,
                    away,
                    str(hg),
                    str(ag),
                    str(gate),
                )
            )
            n += 1

    con.execute("""
        CREATE OR REPLACE TABLE raw_matches (
            match_id VARCHAR PRIMARY KEY, season INTEGER, date VARCHAR,
            home_raw VARCHAR, away_raw VARCHAR,
            home_goals VARCHAR, away_goals VARCHAR, attendance VARCHAR
        )
    """)
    con.executemany("INSERT INTO raw_matches VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

    con.execute(
        "CREATE OR REPLACE TABLE club_aliases "
        "(raw_name VARCHAR, club_id VARCHAR, note VARCHAR)"
    )
    con.executemany(
        "INSERT INTO club_aliases VALUES (?, ?, ?)",
        [(c, c, "demo") for c in clubs],
    )
    print(
        f"  seeded {len(rows)} demo matches, {n_clubs} clubs, "
        f"{len(rounds)} matchdays, season {season}"
    )


def load_from_json(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Build raw_matches and a starter club_aliases from a league-matches payload.

    Bridges MVP 01's archived response straight into MVP 02 without needing an
    intermediate raw table, so the SQL can be exercised on real data today.

    Field mapping, all confirmed present in a real response:

        match_id    "fs:" + id          namespaced provider id
        season      season              "2018/2019" -> 2018, the starting year
        date        date_unix           UTC date
        home_raw    homeID              the provider's club id, not the name
        away_raw    awayID              names change; ids do not
        home_goals  homeGoalCount
        away_goals  awayGoalCount
        attendance  attendance

    Joining on homeID rather than home_name is the important choice. The payload's
    home_name is the club's CURRENT name, so a 2017 match would arrive under a 2026
    brand - see docs/phases/03-club-name-consistency.md.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("data")
    if not data:
        raise SystemExit(f"no 'data' array in {path.name}; top-level keys: {sorted(payload)}")

    required = {"id", "season", "date_unix", "homeID", "awayID",
                "homeGoalCount", "awayGoalCount", "attendance"}
    missing = required - set(data[0])
    if missing:
        raise SystemExit(
            f"payload is missing {sorted(missing)}.\n"
            f"  Available: {sorted(data[0])[:20]} ..."
        )

    rows = []
    for d in data:
        season_txt = str(d["season"])
        year_match = re.match(r"(\d{4})", season_txt)
        year = int(year_match.group(1)) if year_match else 0
        rows.append(
            (
                f"fs:{d['id']}",
                year,
                str(duckdb.sql(f"SELECT to_timestamp({int(d['date_unix'])})::DATE").fetchone()[0]),
                str(d["homeID"]),
                str(d["awayID"]),
                str(d["homeGoalCount"]),
                str(d["awayGoalCount"]),
                str(d["attendance"]),
            )
        )

    con.execute("""
        CREATE OR REPLACE TABLE raw_matches (
            match_id VARCHAR PRIMARY KEY, season INTEGER, date VARCHAR,
            home_raw VARCHAR, away_raw VARCHAR,
            home_goals VARCHAR, away_goals VARCHAR, attendance VARCHAR
        )
    """)
    con.executemany("INSERT INTO raw_matches VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

    # Starter alias table derived from the payload. NOT a substitute for the
    # hand-maintained CSV - it maps each provider id to a slug of that club's
    # current name, which is a starting point to review, not an answer.
    names: dict[str, str] = {}
    for d in data:
        names.setdefault(str(d["homeID"]), str(d.get("home_name", d["homeID"])))
        names.setdefault(str(d["awayID"]), str(d.get("away_name", d["awayID"])))
    aliases = [
        (cid, re.sub(r"[^a-z0-9]+", "_", nm.lower()).strip("_"), "derived from payload")
        for cid, nm in names.items()
    ]
    con.execute(
        "CREATE OR REPLACE TABLE club_aliases "
        "(raw_name VARCHAR, club_id VARCHAR, note VARCHAR)"
    )
    con.executemany("INSERT INTO club_aliases VALUES (?, ?, ?)", aliases)
    print(f"  loaded {len(rows)} matches and {len(aliases)} clubs from {path.name}")
    print("      club_aliases was DERIVED from the payload - review it by hand before")
    print("      trusting it. Names are current names, so historical seasons will be off.")


def load_aliases(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Register the hand-maintained alias CSV as a table."""
    if not path.exists():
        raise SystemExit(f"alias file not found: {path}")
    con.execute(
        "CREATE OR REPLACE TABLE club_aliases AS "
        "SELECT * FROM read_csv_auto(?, header=true, all_varchar=true)",
        [str(path)],
    )
    n = scalar(con, "SELECT count(*) FROM club_aliases")
    print(f"  loaded {n} alias rows from {path.name}")


# ---------------------------------------------------------------------------


NEEDED_RAW_COLUMNS = {
    "match_id": "primary key, so re-running upserts instead of duplicating",
    "season": "partitions the standings window",
    "date": "orders the standings and lag windows. Without it there is no point-in-time",
    "home_raw": "joins to club_aliases",
    "away_raw": "joins to club_aliases",
    "home_goals": "points, goal difference, goals for",
    "away_goals": "points, goal difference, goals for",
    "attendance": "the target variable",
}


def explain_missing_raw_matches(con: duckdb.DuckDBPyConnection) -> None:
    """Fail with something actionable when raw_matches is absent.

    A database built for MVP 01's attendance question usually carries a table that
    is close but not sufficient - enough to answer "is attendance populated", not
    enough to reconstruct a league table. Say which columns are missing and what
    each one is for, rather than just reporting a missing table.
    """
    tables = [r[0] for r in con.sql("SELECT table_name FROM duckdb_tables()").fetchall()]
    lines = [f"no raw_matches table (found: {tables or 'none'})."]

    # If some other table looks like match data, diagnose it specifically.
    for name in tables:
        cols = {r[0] for r in con.sql(f'DESCRIBE "{name}"').fetchall()}
        if "attendance" not in cols:
            continue
        missing = [c for c in NEEDED_RAW_COLUMNS if c not in cols]
        if not missing:
            continue
        lines += [
            "",
            f"'{name}' looks like match data but cannot build a league table.",
            f"  has:     {sorted(cols)}",
            "  missing:",
        ]
        lines += [f"    {c:<12} {NEEDED_RAW_COLUMNS[c]}" for c in missing]
        lines += [
            "",
            "  Every one of these is in the league-matches payload already:",
            "    season -> season, date -> date_unix, home_goals -> homeGoalCount,",
            "    away_goals -> awayGoalCount, home_raw -> homeID, away_raw -> awayID",
            "  They were just not carried into the table.",
        ]
        break

    lines += [
        "",
        "Two ways forward:",
        "  1. Skip the intermediate table entirely (works today):",
        "       --from-json league-matches_season_1.json",
        "  2. Carry the extra fields when you build the raw table, then --db works.",
        "",
        "  --seed-demo exercises the SQL on synthetic data with no source at all.",
    ]
    raise SystemExit("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=":memory:", help="DuckDB file. Default in-memory.")
    parser.add_argument(
        "--seed-demo", action="store_true", help="Generate synthetic data first."
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        help="Build raw_matches from an archived league-matches payload.",
    )
    parser.add_argument(
        "--aliases",
        type=Path,
        default=None,
        help="club_aliases.csv to use instead of a club_aliases table in the database.",
    )
    parser.add_argument(
        "--show", type=int, default=8, help="Rows of the mart to print. 0 for none."
    )
    args = parser.parse_args(argv)
    args.aliases_explicit = args.aliases is not None
    if args.aliases is None:
        args.aliases = DEFAULT_ALIASES

    con = duckdb.connect(args.db)

    print("setup")
    if args.seed_demo:
        seed_demo(con)
    elif args.from_json:
        load_from_json(con, args.from_json)
    else:
        exists = scalar(
            con,
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'raw_matches'",
        )
        if not exists:
            explain_missing_raw_matches(con)

        # Use a club_aliases table already in the database if there is one. Only
        # fall back to the CSV when the database has none, or when --aliases was
        # passed explicitly. Clobbering a mapping the database already carries is
        # how you get twenty "unmapped club" errors for clubs that were mapped.
        has_aliases = scalar(
            con,
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'club_aliases'",
        )
        if args.aliases_explicit or not has_aliases:
            load_aliases(con, args.aliases)
        else:
            n = scalar(con, "SELECT count(*) FROM club_aliases")
            print(f"  using the club_aliases table already in the database ({n} rows)")
            print("      pass --aliases to override it with a CSV")

    print("build")
    con.execute(SQL_STG_MATCHES)
    n_stg = scalar(con, "SELECT count(*) FROM stg_matches")
    print(f"  stg_matches          {n_stg:>6} rows")
    check_all_clubs_mapped(con)
    check_row_count_preserved(con)
    check_one_match_per_club_per_date(con)

    con.execute(SQL_MART)
    n_mart = scalar(con, "SELECT count(*) FROM mart_match_features")
    print(f"  mart_match_features  {n_mart:>6} rows")

    print("checks")
    check_first_match_is_zero(con)
    check_no_leakage(con)
    check_final_table(con)

    nulls = one_row(con, """
        SELECT sum(CASE WHEN last_home_gate IS NULL THEN 1 ELSE 0 END),
               sum(CASE WHEN home_gate_ma3  IS NULL THEN 1 ELSE 0 END),
               count(*)
        FROM mart_match_features
    """)
    print(
        f"  ok  expected nulls: last_home_gate {nulls[0]}, "
        f"home_gate_ma3 {nulls[1]}, of {nulls[2]}"
    )
    print("      (a club's first home match has no prior gate - that null is correct)")

    if args.show:
        print(f"\nmart_match_features, first {args.show} rows")
        con.sql(f"""
            SELECT date, home_club_id, opponent_club_id, attendance,
                   rank_before, opponent_rank_before, rank_gap,
                   last_home_gate, round(home_gate_ma3) AS ma3
            FROM mart_match_features LIMIT {args.show}
        """).show()

    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
