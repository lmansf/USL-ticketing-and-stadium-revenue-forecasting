"""One asset per SQL model, and the asset checks built from the check functions."""

from typing import Any

import duckdb as _duckdb
from dagster import (
    AssetCheckExecutionContext,
    AssetCheckResult,
    AssetCheckSeverity,
    AssetExecutionContext,
    MaterializeResult,
    MetadataValue,
    asset,
    asset_check,
)

from usl.assets.resources import DuckDBResource, run_context
from usl.logging_setup import log_check_result, stage
from usl.transform import runner
from usl.transform.checks import (
    INTERMEDIATE_CHECKS,
    MART_CHECKS,
    STAGING_CHECKS,
    Check,
)

# Upstream assets per model: what the .sql file actually reads. Dagster draws
# the lineage graph from this, so it has to match the SQL, and a test checks
# that every table name a model reads is either an upstream asset or itself
# derived from one.
MODEL_DEPS: dict[str, tuple[str, ...]] = {
    "stg_clubs": ("club_conference",),
    "stg_matches": ("raw_matches", "club_aliases", "ref_config"),
    "stg_weather": ("raw_weather",),
    "int_standings": ("stg_matches", "stg_clubs"),
    "int_stakes": ("int_standings", "stg_matches", "conference_structure", "ref_config"),
    "mart_match_features": ("stg_matches", "stg_weather", "int_standings", "int_stakes", "derbies"),
    "mart_decay_curve": ("mart_match_features",),
}

GROUP_BY_TIER = {"staging": "staging", "intermediate": "intermediate", "mart": "marts"}


def _preview(con: _duckdb.DuckDBPyConnection, model: str) -> str:
    frame = con.execute(f"SELECT * FROM {model} LIMIT 5").df()
    return str(frame.to_markdown(index=False)) if not frame.empty else "(empty)"


def _model_asset(model: str) -> Any:
    tier = runner.TIERS[model]

    @asset(
        name=model,
        group_name=GROUP_BY_TIER[tier],
        compute_kind="duckdb",
        deps=list(MODEL_DEPS[model]),
    )
    def _build(context: AssetExecutionContext, duckdb: DuckDBResource) -> MaterializeResult:
        ctx = run_context(context)
        with duckdb.connect() as con, stage(con, ctx, model) as meta:
            rows = runner.materialise(con, model)
            meta["rows_read"] = rows
            preview = _preview(con, model)
        return MaterializeResult(
            metadata={"rows": rows, "tier": tier, "preview": MetadataValue.md(preview)}
        )

    _build.__doc__ = f"usl/sql/{model}.sql, CREATE OR REPLACE. Tier: {tier}."
    return _build


MODEL_ASSETS = tuple(_model_asset(model) for model in runner.MODELS)

# Which asset each tier's checks hang off. A blocking check that fails stops
# everything downstream of that asset in the same run - the phase-one rule
# "collect within a tier, stop between tiers" with Dagster doing the stopping.
CHECK_ANCHOR: dict[str, str] = {
    "staging": "stg_matches",
    "intermediate": "int_standings",
    "mart": "mart_match_features",
}
# Other assets a tier's checks read, so the checks wait for them too. Only
# assets that are NOT downstream of the anchor can go here: a blocking check
# runs before the anchor's downstream assets, so naming one of those would be
# a cycle. The intermediate and mart checks read nothing beyond their anchor
# and its upstream.
CHECK_EXTRA_DEPS: dict[str, tuple[str, ...]] = {
    "staging": ("stg_clubs", "stg_weather", "conference_structure", "derbies", "stadiums"),
    "intermediate": (),
    "mart": (),
}


def _asset_check(check: Check, tier: str) -> Any:
    @asset_check(
        name=check.__name__,
        asset=CHECK_ANCHOR[tier],
        additional_deps=list(CHECK_EXTRA_DEPS[tier]),
        blocking=True,
        description=(check.__doc__ or "").strip().splitlines()[0],
    )
    def _run(context: AssetCheckExecutionContext, duckdb: DuckDBResource) -> AssetCheckResult:
        ctx = run_context(context)
        with duckdb.connect() as con:
            result = check(con)
            log_check_result(con, ctx, result)
        return AssetCheckResult(
            passed=bool(result.passed),
            severity=AssetCheckSeverity.ERROR,
            metadata={
                "tier": result.tier,
                **{
                    k: MetadataValue.json(v) if isinstance(v, list | dict) else v
                    for k, v in result.metadata.items()
                },
            },
        )

    return _run


ASSET_CHECKS = tuple(
    _asset_check(check, tier)
    for tier, checks in (
        ("staging", STAGING_CHECKS),
        ("intermediate", INTERMEDIATE_CHECKS),
        ("mart", MART_CHECKS),
    )
    for check in checks
)
