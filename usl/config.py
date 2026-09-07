"""Project configuration.

Every tunable that a reader might want to check lives here rather than inline in
SQL or in a function body. Several of these are judgement calls rather than
facts - those are marked, and each one is discussed in
docs/reference/open-questions.md.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Read .env if present. Absent is fine - see has_subscription() below.
load_dotenv()

# --------------------------------------------------------------------------
# Paths
#
# Absolute paths derived from this file's location, so a scheduled task with an
# unexpected working directory still resolves correctly. See
# docs/mvp/05-mvp-schedule.md for why that matters.
# --------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
CACHE_DIR: Path = DATA_DIR / "cache"
# Committed, not gitignored. The only copy of the source data once the
# subscription lapses. See docs/phases/00-data-access-and-the-clock.md
ARCHIVE_DIR: Path = DATA_DIR / "raw_archive"
LOG_DIR: Path = PROJECT_ROOT / "logs"
SQL_DIR: Path = Path(__file__).resolve().parent / "sql"
REF_DIR: Path = Path(__file__).resolve().parent / "ref"
EXTRACT_DIR: Path = PROJECT_ROOT / "tableau" / "extracts"
FIXTURE_DIR: Path = PROJECT_ROOT / "demo" / "fixtures"

DB_PATH: Path = DATA_DIR / "usl.duckdb"

# Hand-maintained reference files. Code, not data - see usl/ref/README.md.
CLUB_ALIASES_CSV: Path = REF_DIR / "club_aliases.csv"
CLUB_CONFERENCE_CSV: Path = REF_DIR / "club_conference.csv"
CONFERENCE_STRUCTURE_CSV: Path = REF_DIR / "conference_structure.csv"
DERBIES_CSV: Path = REF_DIR / "derbies.csv"
STADIUMS_CSV: Path = REF_DIR / "stadiums.csv"


# --------------------------------------------------------------------------
# The DuckDB lock
#
# DuckDB is single-writer. The route taken for the unguided exercise in
# docs/phases/02-duckdb-and-the-lock-problem.md is RETRY, not write-to-temp-
# and-swap: a held lock is retried with exponential backoff and then reported
# with a message that names the holder. The reasoning is in
# docs/reference/build-decisions.md. These are the retry parameters.
# --------------------------------------------------------------------------

LOCK_MAX_ATTEMPTS: int = 5
LOCK_BACKOFF_BASE_SECONDS: float = 2.0  # 2, 4, 8, 16 - about 30 seconds in total


# --------------------------------------------------------------------------
# Seasons
# --------------------------------------------------------------------------

# FootyStats addresses a season id, not a year. The mapping from "USL
# Championship 2019" to its id is discovered from the league-list endpoint and
# written into usl/ref/seasons.csv, which is loaded at runtime.
#
# Rows with an empty season_id are skipped by the backfill and reported, so the
# file doubles as the "what have I not pulled yet" list while the clock runs.
# You cannot rebuild that mapping after access lapses.
# See docs/reference/open-questions.md#season-ids
# The seasons the backfill pulls. seasons.csv is the USL Championship; the EPL
# example season the pipeline was built against lives in seasons.example.csv,
# and USL_SEASONS_CSV points the pipeline at it (tests and demos do this) so one
# database holds one league.
SEASONS_CSV: Path = Path(os.environ.get("USL_SEASONS_CSV") or REF_DIR / "seasons.csv")

# The season currently in progress, or None when the data is archive-only.
#
# None is what makes the freshness check pass on an archived season: with no
# current season configured there is nothing that could be fresh, and the check
# records that reason instead of failing every run for ever. Set
# USL_CURRENT_SEASON in .env on the machine that runs the weekly ingest; it is
# a deployment setting, not a fact about the data, so it is not committed.
CURRENT_SEASON: int | None = (
    int(os.environ["USL_CURRENT_SEASON"])
    if os.environ.get("USL_CURRENT_SEASON", "").strip()
    else None
)

# Approximate season boundaries, used by the freshness check to avoid firing
# every week of the off-season. Month/day only - applied to the current year.
# USL Championship runs roughly March to November.
SEASON_START_MD: tuple[int, int] = (3, 1)
SEASON_END_MD: tuple[int, int] = (11, 15)

# The API returns kick-off as unix seconds (UTC). The match DATE - which drives
# day_of_week, is_weekend and the standings grid - is taken in this zone. For
# USL it is a JUDGEMENT CALL, resolved as US Central: a 7:30pm Pacific kick-off
# is 9:30pm Central and still Saturday, a 7pm Eastern one is 6pm Central, and
# only a Pacific kick-off at or after 10pm crosses midnight, which the league
# does not schedule. One zone rather than a per-club column, because the date
# is right for every real kick-off and a column is one more thing to maintain.
# Exact for the example season too: no EPL kick-off is later than 20:00 UTC.
# See docs/reference/build-decisions.md.
MATCH_TZ: str = "America/Chicago"


@dataclass(frozen=True)
class SeasonRow:
    """One row of usl/ref/seasons.csv."""

    season: int
    season_id: int | None
    note: str


def read_seasons_csv(path: Path | None = None) -> list[SeasonRow]:
    """Read the season-year to FootyStats season_id mapping.

    A blank season_id reads as None rather than failing, because the file is
    meant to hold the seasons you intend to pull before you know their ids.

    Args:
        path: Defaults to SEASONS_CSV.

    Returns:
        Rows in file order.
    """
    rows: list[SeasonRow] = []
    with open(path or SEASONS_CSV, encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            raw_id = (rec.get("season_id") or "").strip()
            rows.append(
                SeasonRow(
                    season=int(rec["season"]),
                    season_id=int(raw_id) if raw_id else None,
                    note=(rec.get("note") or "").strip(),
                )
            )
    return rows


# --------------------------------------------------------------------------
# FootyStats API
#
# The key is a PAID credential read from .env. It is never committed, never
# logged, and never written into an archive filename.
#
# Leaving it empty is a supported mode, not a broken one: the pipeline then runs
# entirely from ARCHIVE_DIR, which is how the finished repo works for anyone
# without a subscription.
# --------------------------------------------------------------------------

FOOTYSTATS_API_KEY: str = os.environ.get("FOOTYSTATS_API_KEY", "")

# FootyStats serves the literal key "example" for the English Premier League
# 2018/19 season. A complete, real season of the same response shape as USL, for
# nothing. Build and test the entire client against this before subscribing.
EXAMPLE_KEY: str = "example"
EXAMPLE_SEASON_ID: int = 1625

REQUEST_TIMEOUT_SECONDS: float = 30.0

# The entry tier allows roughly 1800 requests an hour, so this delay is mostly
# ceremonial - it keeps you far below the ceiling and is politer than bursting.
# The constraint that actually matters is that the window closes, which the
# archive handles rather than the throttle.
REQUEST_DELAY_SECONDS: float = 1.0

# Transient failures only. A 401 means the key is wrong or the subscription has
# lapsed; a 404 means the endpoint or season id is wrong. Neither improves with
# waiting. See docs/phases/09-break-and-fix.md, scenario D2.
FETCH_MAX_ATTEMPTS: int = 3
FETCH_BACKOFF_BASE_SECONDS: float = 2.0


def has_subscription() -> bool:
    """Whether an API key is available for live requests.

    False is a normal, supported state - it means every request must be served
    from the archive. Code that needs to fetch should say so with
    NoSubscriptionError rather than failing with an opaque 401.
    """
    return bool(FOOTYSTATS_API_KEY)


# --------------------------------------------------------------------------
# COVID exclusion
#
# JUDGEMENT CALL, not a fact. Restrictions eased at different times in different
# markets and some 2021 matches were capacity-limited. State in the README that
# this is a range you chose.
# See docs/reference/open-questions.md#the-covid-window
# --------------------------------------------------------------------------

COVID_START: dt.date = dt.date(2020, 3, 1)
COVID_END: dt.date = dt.date(2021, 6, 30)

# Default on for training. Flip to show the difference.
DROP_COVID: bool = True


# --------------------------------------------------------------------------
# Match status
# --------------------------------------------------------------------------

# The status values the provider is known to send, compared lower-cased and
# trimmed. A value outside this set stops the transform naming it
# (checks.played_rows_consistent): a renamed status would otherwise quietly
# un-play every match it touched, and every other check would still pass.
KNOWN_MATCH_STATUSES: tuple[str, ...] = (
    "complete",
    "incomplete",
    "suspended",
    "postponed",
    "canceled",
    "cancelled",
)

# Statuses that mean a fixture will never be played. JUDGEMENT CALL: raw never
# deletes and staging never drops, so such a row stays in stg_matches flagged
# is_void, and is then left out of everything that counts fixtures - the
# standings grid, matches_remaining, the mart, and the forecasts. A suspended
# or postponed fixture is NOT void: it is expected to be replayed and its row
# is updated when it is. See docs/reference/build-decisions.md, phase 06.
VOID_MATCH_STATUSES: tuple[str, ...] = ("canceled", "cancelled")


# --------------------------------------------------------------------------
# Standings and stakes
# --------------------------------------------------------------------------

# This project ranks within conference, not league-wide.
# See docs/phases/04-standings-as-of-match-date.md
RANK_SCOPE: str = "conference"

# Playoff qualifying positions per conference live in
# usl/ref/conference_structure.csv keyed by (season, conference), because the
# number has changed across seasons and a single constant is wrong for some of
# them. This is the fallback for a (season, conference) with no row there.
# None means NO fallback: the stakes features come out null and the
# features_not_null check stops the run naming the column - which is the
# right outcome for a season nobody has looked up yet.
# See docs/reference/open-questions.md#the-playoff-line
DEFAULT_PLAYOFF_SPOTS: int | None = None

# Assumed relegation cutoff for the instrumented, unvalidated feature
# points_from_relegation_line. No relegation exists in USL data, so this number
# is an assumption made visible rather than a measurement. It applies where
# conference_structure.csv leaves relegation_spots blank. Bottom two per
# conference is the assumption; USL has not published the 2028 structure in
# enough detail to do better. State it wherever the feature appears.
# See docs/reference/open-questions.md#the-relegation-line
ASSUMED_RELEGATION_SPOTS: int = 2


# --------------------------------------------------------------------------
# Modelling
# --------------------------------------------------------------------------

TARGET: str = "attendance"

# Chronological split, never random. See docs/phases/07-two-models.md
TEST_FRACTION: float = 0.2

RANDOM_STATE: int = 42

# Seeds for the run-to-run variance estimate (exercise 7.2). The first is the
# one whose metrics are written to model_metrics; the spread across all of them
# is written to model_variance so the A-to-B gap can be read against noise.
VARIANCE_SEEDS: tuple[int, ...] = (42, 7, 19, 101)

# XGBoost keyword arguments. Near-defaults are deliberate - tuning is not where
# the value is in this project. subsample and colsample_bytree are the one
# addition, and not for accuracy: without them the hist booster is fully
# deterministic and every seed in VARIANCE_SEEDS produces the identical model,
# so the run-to-run noise floor that exercise 7.2 needs would read as exactly
# zero. Row and column subsampling is what makes the seed matter.
XGB_PARAMS: dict[str, object] = {
    "n_estimators": 400,
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": RANDOM_STATE,
}

# "gain", not "weight". Weight counts splits, which flatters high-cardinality
# features. See docs/phases/07-two-models.md, exercise 7.1.
IMPORTANCE_TYPE: str = "gain"


# --------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------

# Freshness check: how stale the latest match may be, in season, before failing.
MAX_MATCH_AGE_DAYS: int = 10

# Lag features (last_home_gate, the moving averages) carry over from the
# previous season, because support level does. They do not carry over a hole:
# a gate older than this many days is not history, and the moving averages
# restart after it. 400 days spans one off-season (about 150 to 200 days
# between a club's last home match and its next opener) and no more. The USL
# archive has no gates for 2021 to 2023, so without this a 2024 opener would
# inherit its club's 2019 crowd. Nulls inside the gap are allowed nulls.
LAG_MAX_GAP_DAYS: int = 400

# Feature columns permitted to contain nulls, and why. Everything else failing
# the not-null check is a bug. See docs/phases/05-sql-layer.md
#
# The null policy, decided once (demo scenario D4): the check fails the run on
# any null outside this set; nulls inside it are passed to XGBoost, which learns
# a default split direction for them. No imputation anywhere.
ALLOWED_NULL_FEATURES: frozenset[str] = frozenset(
    {
        "last_home_gate",  # a club's first ever home match has no prior gate
        "home_gate_ma3",  # ditto, plus the two after it
        "home_gate_ma5",
        "same_fixture_last_season",  # first meeting between two clubs
        # Phase two weather. Null until the Open-Meteo backfill is archived, and
        # null for a fixture beyond the forecast horizon; both are expected, and
        # an all-null weather column is dropped before training rather than fed
        # to the model as a constant.
        "temp_max_c",
        "temp_min_c",
        "precipitation_mm",
        "wind_max_kmh",
        "cloud_cover_pct",
    }
)


# --------------------------------------------------------------------------
# Phase two: weather via Open-Meteo
#
# On by default. The example season's weather is archived under
# data/raw_archive/open-meteo-*, so the archive-only run and CI fetch nothing
# and the weather columns are populated. Set USL_WEATHER_ENABLED=0 in .env to
# turn the stage off; it then records a skip and the weather columns are null.
# With it on, a season already archived costs no request, and only the
# observations and forecasts still missing go to the network. See
# docs/phases/12-phase-two-weather.md
# --------------------------------------------------------------------------

WEATHER_ENABLED: bool = os.environ.get("USL_WEATHER_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
    "",
)

# --------------------------------------------------------------------------
# Phase two: the Dagster schedule (usl/defs.py). Tuesday 06:00, same day as
# the phase-one scheduled task and for the same reason. Cron in the timezone
# below; set it to the machine's zone when you register the schedule.
# --------------------------------------------------------------------------

SCHEDULE_CRON: str = "0 6 * * 2"
# The zone the cron is read in: Central, the same zone as the match date, so
# 06:00 is Tuesday morning where the league plays. USL_SCHEDULE_TZ overrides it.
SCHEDULE_TZ: str = os.environ.get("USL_SCHEDULE_TZ", "").strip() or "America/Chicago"

# Open-Meteo's free tier limits requests per minute, and weights a multi-year
# archive range as many requests, so a backfill trips the limit every ten or so.
# A 429 is waited out and retried, up to this many times per request, and every
# request is spaced by the delay. Neither costs anything but time.
WEATHER_REQUEST_DELAY_SECONDS: float = 1.0
WEATHER_RATE_LIMIT_WAIT_SECONDS: float = 61.0
WEATHER_RATE_LIMIT_WAITS: int = 5

# Open-Meteo's forecast horizon. A fixture further out than this has no weather
# row until it comes inside the window.
WEATHER_FORECAST_DAYS: int = 16

# The observed archive trails real time by about five days (ERA5 reanalysis).
# A played match older than this that still carries forecast weather is
# overdue for the archive and the played_weather_is_observed check names it;
# a younger one is simply not available yet.
WEATHER_ARCHIVE_LAG_DAYS: int = 7


# --------------------------------------------------------------------------
# Tableau extracts
# --------------------------------------------------------------------------

# What Tableau needs, not everything. raw_matches has no place in a dashboard.
EXTRACT_TABLES: tuple[str, ...] = (
    "mart_match_features",
    "mart_decay_curve",
    "int_standings",
    "int_stakes",
    "stg_clubs",
    "predictions",
    "model_metrics",
    "model_cv",
    "model_variance",
    "feature_importance",
    "run_log",
    "check_log",
)


def season_start(year: int) -> dt.date:
    """Season start date for a given year, from SEASON_START_MD."""
    return dt.date(year, *SEASON_START_MD)


def season_end(year: int) -> dt.date:
    """Season end date for a given year, from SEASON_END_MD."""
    return dt.date(year, *SEASON_END_MD)


def in_season(on: dt.date | None = None) -> bool:
    """Whether a date falls inside the playing season.

    Used by the freshness check so an eighty-day gap in January reads as correct
    rather than as a failure. See docs/phases/02-duckdb-and-the-lock-problem.md.

    Args:
        on: Date to test. Defaults to today.

    Returns:
        True if the date is within the season window.
    """
    d = on or dt.date.today()
    return season_start(d.year) <= d <= season_end(d.year)
