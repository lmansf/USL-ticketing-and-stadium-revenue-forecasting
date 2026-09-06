"""The training asset: five tables from one call to train_all."""

from collections.abc import Iterator
from typing import Any

from dagster import AssetExecutionContext, AssetOut, MaterializeResult, MetadataValue, multi_asset

from usl.assets.resources import DuckDBResource, run_context
from usl.db import row_count
from usl.logging_setup import stage
from usl.models.train import OUTPUT_TABLES, train_all


@multi_asset(
    outs={table: AssetOut(group_name="models") for table in OUTPUT_TABLES},
    deps=["mart_match_features"],
    compute_kind="xgboost",
    can_subset=False,
)
def trained_models(
    context: AssetExecutionContext, duckdb: DuckDBResource
) -> Iterator[MaterializeResult]:
    """Both XGBoost models, the naive baseline, seed variance and expanding-window CV.

    One training call writes all five tables, so they are one multi-asset:
    predictions, model_metrics, feature_importance, model_variance, model_cv.
    The MAE per model is on model_metrics so it plots over runs.
    """
    ctx = run_context(context)
    with duckdb.connect() as con, stage(con, ctx, "train") as meta:
        summary = train_all(con, ctx.started_at.date())
        meta["rows_read"] = summary["n_played"]
        counts = {table: row_count(con, table) for table in OUTPUT_TABLES}
    mae = {name: float(value) for name, value in summary["mae"].items()}
    shared = {
        "split_date": str(summary["split_date"]),
        "n_train": int(summary["n_train"]),
        "n_test": int(summary["n_test"]),
        "n_future": int(summary["n_future"]),
        "all_null_features": MetadataValue.json(list(summary["all_null_features"])),
    }
    for table in OUTPUT_TABLES:
        metadata: dict[str, Any] = {"rows": counts[table], **shared}
        if table == "model_metrics":
            metadata.update({f"mae_{name}": value for name, value in mae.items()})
        if table == "model_variance":
            metadata.update(
                {
                    f"seed_spread_{name}": float(max(v.values()) - min(v.values()))
                    for name, v in summary["variance"].items()
                }
            )
        yield MaterializeResult(asset_key=table, metadata=metadata)
