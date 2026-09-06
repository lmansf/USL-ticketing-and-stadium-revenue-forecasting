"""Phase two Dagster: the asset graph materialises the example season in-process.

These run the real Dagster machinery (dagster.materialize, an ephemeral
instance) over the same functions the CLI calls, against a database under
tmp_path and the committed example archive. Nothing here needs a webserver.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

dagster = pytest.importorskip("dagster")

from dagster import AssetKey, ExecuteInProcessResult, in_process_executor, materialize  # noqa: E402

from tests.test_sql_layer import ALL_CHECKS  # noqa: E402
from usl import config  # noqa: E402
from usl.assets.resources import DuckDBResource  # noqa: E402
from usl.assets.sql import MODEL_DEPS  # noqa: E402
from usl.defs import ALL_ASSETS, defs, weekly_job, weekly_schedule  # noqa: E402
from usl.transform import runner  # noqa: E402


@pytest.fixture(autouse=True)
def archive_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key, no current season, no weather: the run every clone can do."""
    monkeypatch.setattr(config, "FOOTYSTATS_API_KEY", "")
    monkeypatch.setattr(config, "CURRENT_SEASON", None)
    monkeypatch.setattr(config, "WEATHER_ENABLED", False)


def run_graph(
    tmp_path: Path, *, raise_on_error: bool = True
) -> tuple[ExecuteInProcessResult, Path]:
    db = tmp_path / "dagster.duckdb"
    resource = DuckDBResource(path=str(db), extract_dir=str(tmp_path / "extracts"))
    result = materialize(
        assets=[*ALL_ASSETS, *defs.asset_checks],
        resources={"duckdb": resource},
        raise_on_error=raise_on_error,
    )
    return result, db


def materialized_keys(result: ExecuteInProcessResult) -> set[str]:
    return {".".join(e.asset_key.path) for e in result.get_asset_materialization_events()}


@pytest.mark.slow
def test_the_whole_graph_materialises_the_example_season(tmp_path: Path) -> None:
    """Every asset, every check, one run id, and the run log written under it."""
    result, db = run_graph(tmp_path)
    assert result.success
    keys = materialized_keys(result)
    expected = {
        "raw_matches",
        "club_aliases",
        "club_conference",
        "conference_structure",
        "derbies",
        "stadiums",
        "ref_config",
        "raw_weather",
        *runner.MODELS,
        "predictions",
        "model_metrics",
        "feature_importance",
        "model_variance",
        "model_cv",
        "tableau_extracts",
    }
    assert keys == expected

    evaluations = result.get_asset_check_evaluations()
    assert len(evaluations) == len(ALL_CHECKS)
    assert all(e.passed for e in evaluations), [e.check_name for e in evaluations if not e.passed]
    assert {e.check_name for e in evaluations} == {c.__name__ for c in ALL_CHECKS}

    # metadata that plots over time is on the materialization
    raw = result.asset_materializations_for_node("raw_matches")[0]
    assert raw.metadata["rows"].value == 380
    assert raw.metadata["rows_inserted"].value == 380
    assert raw.metadata["null_attendance_pct"].value == 0.0
    metrics = [
        m
        for m in result.asset_materializations_for_node("trained_models")
        if m.asset_key == AssetKey("model_metrics")
    ][0]
    assert set(metrics.metadata) >= {"mae_baseline", "mae_prorel", "mae_naive_club_mean"}
    weather = result.asset_materializations_for_node("raw_weather")[0]
    assert weather.metadata["skipped"].value is True

    con = duckdb.connect(str(db), read_only=True)
    try:
        run_ids = con.execute("SELECT DISTINCT run_id FROM run_log").fetchall()
        assert run_ids == [(result.run_id,)]
        stages = {
            r[0]
            for r in con.execute("SELECT stage FROM run_log WHERE status = 'success'").fetchall()
        }
        assert {"raw_matches", "stg_matches", "mart_match_features", "train", "export"} <= stages
        checks = con.execute(
            "SELECT count(*), bool_and(passed) FROM check_log WHERE run_id = ?", [result.run_id]
        ).fetchone()
        assert checks == (len(ALL_CHECKS), True)
        rows = con.execute("SELECT count(*) FROM mart_match_features").fetchone()
        assert rows == (380,)
    finally:
        con.close()
    assert (tmp_path / "extracts" / "predictions_with_band.csv").exists()


@pytest.mark.slow
def test_a_failing_blocking_check_stops_downstream_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With one club unmapped the staging checks fail and nothing below staging is built."""
    from usl.transform import reference

    aliases = pd.read_csv(config.CLUB_ALIASES_CSV)
    without_city = aliases[aliases["raw_name"].astype(str) != "93"]  # Manchester City's id
    path = tmp_path / "club_aliases.csv"
    without_city.to_csv(path, index=False)
    monkeypatch.setitem(reference.REFERENCE_CSVS, "club_aliases", path)

    result, db = run_graph(tmp_path, raise_on_error=False)
    assert not result.success
    keys = materialized_keys(result)
    assert "stg_matches" in keys
    assert "int_standings" not in keys
    assert "mart_match_features" not in keys
    assert "predictions" not in keys
    failed = {e.check_name for e in result.get_asset_check_evaluations() if not e.passed}
    assert "all_clubs_mapped" in failed
    con = duckdb.connect(str(db), read_only=True)
    try:
        logged = con.execute(
            "SELECT passed FROM check_log WHERE run_id = ? AND check_name = 'all_clubs_mapped'",
            [result.run_id],
        ).fetchone()
        assert logged == (False,)
    finally:
        con.close()


def test_model_dependencies_match_the_tables_each_sql_file_reads() -> None:
    """The lineage graph is drawn from MODEL_DEPS; it has to agree with the SQL."""
    import re

    tables = set(runner.MODELS) | {
        "raw_matches",
        "raw_weather",
        "club_aliases",
        "club_conference",
        "conference_structure",
        "derbies",
        "stadiums",
        "ref_config",
    }
    for model in runner.MODELS:
        sql = (config.SQL_DIR / f"{model}.sql").read_text(encoding="utf-8")
        body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        read = {t for t in re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)", body) if t in tables}
        assert read == set(MODEL_DEPS[model]), (model, read, MODEL_DEPS[model])


def test_the_weekly_job_is_serial_and_scheduled_for_tuesday() -> None:
    assert weekly_schedule.cron_schedule == config.SCHEDULE_CRON == "0 6 * * 2"
    assert weekly_schedule.job_name == weekly_job.name == "weekly"
    assert defs.executor is in_process_executor
    resolved = defs.resolve_job_def("weekly")
    assert resolved.executor_def is in_process_executor
    assert len(defs.asset_checks) == len(ALL_CHECKS)
