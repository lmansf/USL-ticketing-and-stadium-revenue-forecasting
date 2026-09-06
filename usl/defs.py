"""Dagster definitions: the asset graph, its checks, the weekly job and schedule.

    dagster dev -m usl.defs

Phase two. The assets under usl/assets/ wrap the same functions as
python -m usl.run; nothing here is a second implementation of anything.

Serial on purpose. DuckDB is single-writer and every asset opens the one
file, so the job runs on the in-process executor: one asset at a time, in
dependency order, in one process. Parallel assets would be the phase-02 lock
problem with more ways to hit it, for a pipeline that finishes in seconds.

See docs/phases/11-phase-two-dagster.md
"""

from dagster import (
    AssetSelection,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
    in_process_executor,
)

from usl import config
from usl.assets.export import tableau_extracts
from usl.assets.models import trained_models
from usl.assets.raw import REFERENCE_ASSETS, raw_matches
from usl.assets.resources import DuckDBResource
from usl.assets.sql import ASSET_CHECKS, MODEL_ASSETS
from usl.assets.weather import raw_weather

ALL_ASSETS = [
    raw_matches,
    *REFERENCE_ASSETS,
    raw_weather,
    *MODEL_ASSETS,
    trained_models,
    tableau_extracts,
]

weekly_job = define_asset_job(
    name="weekly",
    selection=AssetSelection.all(),
    description="The Tuesday run: every asset in dependency order, checks between tiers.",
)

# Tuesday morning: matches cluster on Saturday with some midweek fixtures, so
# by Tuesday the weekend is posted and settled. Same day as the phase-one task.
weekly_schedule = ScheduleDefinition(
    name="weekly_tuesday",
    job=weekly_job,
    cron_schedule=config.SCHEDULE_CRON,
    execution_timezone=config.SCHEDULE_TZ,
)

defs = Definitions(
    assets=ALL_ASSETS,
    asset_checks=list(ASSET_CHECKS),
    jobs=[weekly_job],
    schedules=[weekly_schedule],
    resources={"duckdb": DuckDBResource(path=str(config.DB_PATH))},
    executor=in_process_executor,
)
