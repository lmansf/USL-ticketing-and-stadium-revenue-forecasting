"""The Tableau extracts asset."""

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from usl.assets.resources import DuckDBResource, run_context
from usl.export.extracts import export_all
from usl.logging_setup import stage
from usl.models.train import OUTPUT_TABLES


@asset(
    group_name="export",
    compute_kind="tableau",
    deps=[*OUTPUT_TABLES, "mart_decay_curve", "int_standings", "int_stakes", "stg_clubs"],
)
def tableau_extracts(context: AssetExecutionContext, duckdb: DuckDBResource) -> MaterializeResult:
    """CSV extracts under tableau/extracts/, plus predictions_with_band.csv."""
    ctx = run_context(context)
    with duckdb.connect() as con, stage(con, ctx, "export") as meta:
        paths = export_all(con, duckdb.extract_path)
        meta["rows_read"] = len(paths)
    return MaterializeResult(
        metadata={
            "files": len(paths),
            "directory": str(duckdb.extract_path),
            "written": MetadataValue.json([p.name for p in paths]),
        }
    )
