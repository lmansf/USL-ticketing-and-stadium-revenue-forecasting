"""Project configuration.

Every tunable that a reader might want to check lives here rather than inline in
SQL or in a function body. Several of these are judgement calls rather than
facts - those are marked, and each one is discussed in
docs/reference/open-questions.md.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

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
LOG_DIR: Path = PROJECT_ROOT / "logs"
SQL_DIR: Path = Path(__file__).resolve().parent / "sql"
REF_DIR: Path = Path(__file__).resolve().parent / "ref"
EXTRACT_DIR: Path = PROJECT_ROOT / "tableau" / "extracts"
FIXTURE_DIR: Path = PROJECT_ROOT / "demo" / "fixtures"

DB_PATH: Path = DATA_DIR / "usl.duckdb"
DB_TMP_PATH: Path = DATA_DIR / "usl.duckdb.tmp"


# --------------------------------------------------------------------------
# Seasons
# --------------------------------------------------------------------------

# TODO: verify the available season range on the source site and fill this in.
# The guide says "all nine available seasons" without naming them, deliberately -
# confirm it yourself rather than trusting a value written here.
# See docs/reference/open-questions.md#which-nine-seasons
SEASONS: list[int] = []

# TODO: the season currently in progress, or None outside the season.
# Used to decide whether a cached page may be reused (a completed season never
# changes; the current one changes weekly).
CURRENT_SEASON: int | None = None

# TODO: approximate season boundaries, used by the freshness check to avoid
# firing every week of the off-season. Month/day only - applied to the current year.
SEASON_START_MD: tuple[int, int] = (3, 1)
SEASON_END_MD: tuple[int, int] = (11, 15)


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------

# TODO: set a real User-Agent that identifies you and gives a contact.
USER_AGENT: str = "usl-attendance-research/0.1 (contact: TODO)"

REQUEST_TIMEOUT_SECONDS: float = 30.0

# Politeness delay between requests during the backfill.
REQUEST_DELAY_SECONDS: float = 2.0

# Transient failures only. A 404 is not transient and must not be retried -
# see docs/phases/09-break-and-fix.md, scenario D2.
FETCH_MAX_ATTEMPTS: int = 3
FETCH_BACKOFF_BASE_SECONDS: float = 2.0

# Column names the parser requires. Missing raises; extra warns.
# See docs/phases/01-scrape-to-raw.md, exercise 1.1.
# TODO: confirm against the live page before relying on these.
EXPECTED_COLUMNS: frozenset[str] = frozenset(
    {"date", "home", "away", "score", "attendance"}
)


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
# Standings and stakes
# --------------------------------------------------------------------------

# This project ranks within conference, not league-wide.
# See docs/phases/04-standings-as-of-match-date.md
RANK_SCOPE: str = "conference"

# TODO: playoff qualifying positions per conference. This has changed across
# seasons, so a single number is wrong for some of them. Consider moving to a
# reference file keyed by (season, conference).
# See docs/reference/open-questions.md#the-playoff-line
PLAYOFF_SPOTS_PER_CONFERENCE: int | None = None

# TODO: assumed relegation cutoff for the instrumented, unvalidated feature
# points_from_relegation_line. No relegation exists in the data, so this number
# is an assumption you are making visible rather than a measurement.
# See docs/reference/open-questions.md#the-relegation-line
ASSUMED_RELEGATION_SPOTS: int | None = None


# --------------------------------------------------------------------------
# Modelling
# --------------------------------------------------------------------------

TARGET: str = "attendance"

# Chronological split, never random. See docs/phases/07-two-models.md
TEST_FRACTION: float = 0.2

RANDOM_STATE: int = 42

# XGBoost keyword arguments. Defaults are deliberate - tuning is not where the
# value is in this project.
XGB_PARAMS: dict[str, object] = {
    "n_estimators": 400,
    "max_depth": 6,
    "learning_rate": 0.05,
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

# Feature columns permitted to contain nulls, and why. Everything else failing
# the not-null check is a bug. See docs/phases/05-sql-layer.md
ALLOWED_NULL_FEATURES: frozenset[str] = frozenset(
    {
        "last_home_gate",  # a club's first ever home match has no prior gate
        "home_gate_ma3",  # ditto, plus the two after it
        "home_gate_ma5",
        "same_fixture_last_season",  # first meeting between two clubs
    }
)


# --------------------------------------------------------------------------
# Tableau extracts
# --------------------------------------------------------------------------

EXTRACT_TABLES: tuple[str, ...] = (
    "mart_match_features",
    "int_standings",
    "predictions",
    "model_metrics",
    "feature_importance",
    "run_log",
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
