"""Feature lists and their evidential classification.

The two models are built by selecting from these lists. Nothing else differs
between them - same mart, same rows, same training call. That is what makes any
difference in error attributable to the pro-rel features rather than to a
difference in the data.

The classification is data rather than prose so the dashboard can colour by it
and so the labelling cannot drift away from the feature list. See
docs/phases/06-features.md#the-honesty-note.
"""

from __future__ import annotations

from enum import StrEnum


class Evidence(StrEnum):
    """How much weight a feature's finding can carry.

    MEASURED: a real finding on real data.

    PROXY: measured, but a partial proxy for the thing we care about. A playoff
        race measures upside stakes; relegation measures downside, existential
        stakes, and European evidence suggests those do not move fans
        identically. Shows the mechanism exists; does not size the effect.

    INSTRUMENTED: built and logged, but with no ground truth in the data because
        the thing it measures has not happened yet. A forward-looking instrument,
        not a predictor. Label it as such wherever it appears.
    """

    MEASURED = "measured"
    PROXY = "proxy"
    INSTRUMENTED = "instrumented"


# --------------------------------------------------------------------------
# Family 1: calendar, lag, and (phase two) weather
#
# Weather is a shared feature, not a pro-rel one, so it sits in the base list
# where both models pick it up.
# --------------------------------------------------------------------------

CALENDAR_FEATURES: tuple[str, ...] = (
    "day_of_week",
    "month",
    "is_weekend",
    "is_midweek",
)

LAG_FEATURES: tuple[str, ...] = (
    "last_home_gate",
    "home_gate_ma3",
    "home_gate_ma5",
    "same_fixture_last_season",
)

# Phase two. Match-day weather at the home ground from Open-Meteo, observed
# for a played match and forecast for a coming one. Null until the backfill is
# archived; an all-null weather column is dropped before training.
WEATHER_FEATURES: tuple[str, ...] = (
    "temp_max_c",
    "temp_min_c",
    "precipitation_mm",
    "wind_max_kmh",
    "cloud_cover_pct",
)

# --------------------------------------------------------------------------
# Family 2: match context
# --------------------------------------------------------------------------

CONTEXT_FEATURES: tuple[str, ...] = (
    "opponent_club_id",
    "is_derby",
    "matches_remaining",
    "is_season_opener",
    "is_final_home_match",
)

# --------------------------------------------------------------------------
# Family 3: pro-rel - the differentiated ones
# --------------------------------------------------------------------------

PROREL_FEATURES: tuple[str, ...] = (
    "rank_before",
    "opponent_rank_before",
    "rank_gap",
    "points_from_playoff_line",
    "is_mathematically_live",
    "matches_since_elimination",
    "points_from_relegation_line",
)

# --------------------------------------------------------------------------
# The two models
# --------------------------------------------------------------------------

BASE_FEATURES: tuple[str, ...] = (
    CALENDAR_FEATURES + LAG_FEATURES + WEATHER_FEATURES + CONTEXT_FEATURES
)

MODEL_FEATURES: dict[str, tuple[str, ...]] = {
    "baseline": BASE_FEATURES,
    "prorel": BASE_FEATURES + PROREL_FEATURES,
}

# --------------------------------------------------------------------------
# Classification
#
# Every feature above appears here exactly once. A test enforces that in both
# directions, so a feature added to a family without a classification fails
# rather than silently defaulting.
# --------------------------------------------------------------------------

EVIDENCE: dict[str, Evidence] = {
    # Calendar and lag - findings.
    "day_of_week": Evidence.MEASURED,
    "month": Evidence.MEASURED,
    "is_weekend": Evidence.MEASURED,
    "is_midweek": Evidence.MEASURED,
    "last_home_gate": Evidence.MEASURED,
    "home_gate_ma3": Evidence.MEASURED,
    "home_gate_ma5": Evidence.MEASURED,
    "same_fixture_last_season": Evidence.MEASURED,
    # Weather - findings, once the backfill is archived.
    "temp_max_c": Evidence.MEASURED,
    "temp_min_c": Evidence.MEASURED,
    "precipitation_mm": Evidence.MEASURED,
    "wind_max_kmh": Evidence.MEASURED,
    "cloud_cover_pct": Evidence.MEASURED,
    # Match context - findings.
    "opponent_club_id": Evidence.MEASURED,
    "is_derby": Evidence.MEASURED,
    "matches_remaining": Evidence.MEASURED,
    "is_season_opener": Evidence.MEASURED,
    "is_final_home_match": Evidence.MEASURED,
    # The dead-rubber counterfactual is measured: real attendance on
    # eliminated-club home matches across nine seasons.
    "matches_since_elimination": Evidence.MEASURED,
    # Table position - measured, but a partial proxy for relegation stakes.
    "rank_before": Evidence.PROXY,
    "opponent_rank_before": Evidence.PROXY,
    "rank_gap": Evidence.PROXY,
    "points_from_playoff_line": Evidence.PROXY,
    "is_mathematically_live": Evidence.PROXY,
    # No relegation exists in the data. No ground truth, by construction.
    "points_from_relegation_line": Evidence.INSTRUMENTED,
}


# --------------------------------------------------------------------------
# Mart columns that are not features
#
# Keys, the target, and the two flags the training step filters on. The mart
# is exactly these plus all_features(), in that order; tests/test_features.py
# enforces both directions so a column cannot be built without being named
# here and a feature cannot be named without being built.
# --------------------------------------------------------------------------

NON_FEATURE_COLUMNS: tuple[str, ...] = (
    "match_id",
    "season",
    "date",
    "home_club_id",
    "attendance",  # the target. Null for a fixture not yet played
    "is_played",
    "is_covid_affected",
    "weather_source",  # 'archive', 'forecast', or null when no weather row
    "weather_horizon_days",  # days out the forecast was made; null for an observation
)


def is_prorel(feature: str) -> bool:
    """Whether a feature belongs to the pro-rel family.

    Drives the colour split in the Tableau feature-importance chart and the
    is_prorel column in the feature_importance table.

    Args:
        feature: Feature name.

    Returns:
        True if the feature is only in the prorel model.
    """
    return feature in PROREL_FEATURES


def all_features() -> tuple[str, ...]:
    """Every feature across all families, in a stable order."""
    return BASE_FEATURES + PROREL_FEATURES


def mart_columns() -> tuple[str, ...]:
    """The exact column list of mart_match_features, keys first."""
    return NON_FEATURE_COLUMNS + all_features()
