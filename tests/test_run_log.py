"""Run and check logging.

Logging is a first-class feature here, so it gets tests.

Doc: docs/reference/logging-and-run-metadata.md
"""

from __future__ import annotations

import duckdb
import pytest


def test_stage_start_writes_a_running_row(con: duckdb.DuckDBPyConnection) -> None:
    """Written at start, not only at the end.

    A process killed by the OS never gets to write a terminal status. The
    leftover 'running' row is how you tell "crashed hard" from "never started".
    """
    pytest.skip("TODO")


def test_stage_finish_updates_rather_than_inserting(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """One row per stage per run, not two."""
    pytest.skip("TODO")


def test_failed_stage_records_the_exception(con: duckdb.DuckDBPyConnection) -> None:
    """error_type and error_message, so the log answers 'what went wrong'."""
    pytest.skip("TODO")


def test_all_stages_of_a_run_share_a_run_id(con: duckdb.DuckDBPyConnection) -> None:
    """So 'every stage of last Tuesday's failed run' is one query."""
    pytest.skip("TODO")


def test_passing_checks_are_logged_too(con: duckdb.DuckDBPyConnection) -> None:
    """A check that has passed for six weeks and starts failing is a signal.

    Only logging failures gives you no baseline to notice that against.
    """
    pytest.skip("TODO")
