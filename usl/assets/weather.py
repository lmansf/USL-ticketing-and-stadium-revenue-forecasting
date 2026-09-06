"""The raw_weather asset: Open-Meteo observations and forecasts."""

from dagster import AssetExecutionContext, MaterializeResult, asset

from usl import config
from usl.assets.resources import DuckDBResource, run_context
from usl.logging_setup import stage
from usl.weather.refresh import refresh


@asset(
    group_name="ingest",
    compute_kind="open-meteo",
    deps=["raw_matches", "club_aliases", "club_conference", "stadiums", "ref_config"],
)
def raw_weather(context: AssetExecutionContext, duckdb: DuckDBResource) -> MaterializeResult:
    """Match-day weather at the home ground, observed where the archive has it.

    Skipped, and recorded as such, unless USL_WEATHER_ENABLED is set. The
    weather features are null until the backfill has been archived.
    """
    ctx = run_context(context)
    with duckdb.connect() as con, stage(con, ctx, "raw_weather") as meta:
        stats = refresh(con, today=ctx.started_at.date())
        meta.update(stats.as_metadata())
        rows_row = con.execute("SELECT count(*) FROM raw_weather").fetchone()
        rows = int(rows_row[0]) if rows_row else 0
    return MaterializeResult(
        metadata={
            "rows": rows,
            "enabled": bool(config.WEATHER_ENABLED),
            **{k.removeprefix("weather_"): v for k, v in stats.as_metadata().items()},
        }
    )
