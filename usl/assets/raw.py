"""Ingest assets: the FootyStats seasons and the reference CSVs."""

from typing import Any

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from usl import config
from usl.assets.resources import DuckDBResource, run_context
from usl.load.raw import backfill, load_season, raw_summary
from usl.logging_setup import LoadStats, stage
from usl.transform import reference


def season_ids() -> list[int]:
    """Every season id filled in under usl/ref/seasons.csv."""
    return [row.season_id for row in config.read_seasons_csv() if row.season_id is not None]


def current_season_id() -> int | None:
    """The season id of config.CURRENT_SEASON, if both are set."""
    if config.CURRENT_SEASON is None:
        return None
    for row in config.read_seasons_csv():
        if row.season == config.CURRENT_SEASON:
            return row.season_id
    return None


def load_metadata(stats: LoadStats, summary: dict[str, Any]) -> dict[str, Any]:
    """The materialization metadata for a load: the split plus freshness."""
    return {
        "rows": int(summary["rows"]),
        "rows_inserted": stats.inserted,
        "rows_updated": stats.updated,
        "rows_unchanged": stats.unchanged,
        "seasons": MetadataValue.json(summary["seasons"]),
        "max_match_date": str(summary["max_match_date"]),
        "null_attendance_pct": float(summary["null_attendance_pct"] or 0.0),
    }


@asset(group_name="ingest", compute_kind="footystats")
def raw_matches(context: AssetExecutionContext, duckdb: DuckDBResource) -> MaterializeResult:
    """Every season in seasons.csv, upserted into raw_matches.

    Completed seasons are archive hits and cost nothing; the season in
    progress, when config.CURRENT_SEASON is set, is pulled as a dated snapshot
    so each weekly run is its own archive entry. The insert/update/unchanged
    split is the idempotency evidence, and it is what plots over time.
    """
    ctx = run_context(context)
    with duckdb.connect() as con, stage(con, ctx, "raw_matches") as meta:
        stats = backfill(con, season_ids())
        current = current_season_id()
        if current is not None:
            stats = stats + load_season(con, current, snapshot=ctx.started_at.date())
        summary = raw_summary(con)
        metadata = load_metadata(stats, summary)
        meta.update(
            {
                "rows_read": metadata["rows"],
                "rows_inserted": stats.inserted,
                "rows_updated": stats.updated,
                "rows_unchanged": stats.unchanged,
                "seasons": summary["seasons"],
                "max_match_date": summary["max_match_date"],
                "null_attendance_pct": summary["null_attendance_pct"],
            }
        )
    return MaterializeResult(metadata=metadata)


def _reference_asset(name: str) -> Any:
    @asset(name=name, group_name="reference", compute_kind="csv")
    def _load(context: AssetExecutionContext, duckdb: DuckDBResource) -> MaterializeResult:
        ctx = run_context(context)
        with duckdb.connect() as con, stage(con, ctx, name) as meta:
            rows = reference.read_reference_csv(con, name, reference.REFERENCE_CSVS[name])
            meta["rows_read"] = rows
        return MaterializeResult(
            metadata={"rows": rows, "path": str(reference.REFERENCE_CSVS[name])}
        )

    _load.__doc__ = f"usl/ref/{name}.csv as a table, every column text, keys normalised."
    return _load


# One asset per hand-maintained CSV, so the lineage graph shows which table
# each model actually depends on.
club_aliases = _reference_asset("club_aliases")
club_conference = _reference_asset("club_conference")
conference_structure = _reference_asset("conference_structure")
derbies = _reference_asset("derbies")
stadiums = _reference_asset("stadiums")


@asset(group_name="reference", compute_kind="config")
def ref_config(context: AssetExecutionContext, duckdb: DuckDBResource) -> MaterializeResult:
    """The one-row table of config values the SQL reads."""
    ctx = run_context(context)
    with duckdb.connect() as con, stage(con, ctx, "ref_config"):
        reference.create_ref_config(con)
    return MaterializeResult(
        metadata={
            "match_tz": config.MATCH_TZ,
            "covid_start": config.COVID_START.isoformat(),
            "covid_end": config.COVID_END.isoformat(),
        }
    )


REFERENCE_ASSETS = (
    club_aliases,
    club_conference,
    conference_structure,
    derbies,
    stadiums,
    ref_config,
)
