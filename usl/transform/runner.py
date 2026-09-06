"""Execute the SQL layer in dependency order.

Three tiers, kept genuinely separate. Raw is a cache of the internet; staging is
where cleaning is reviewable in a diff; the mart is the only thing the model sees.

Checks run after the tier they cover: collected within a tier so one run
reports everything wrong at that level, stopped between tiers so a broken
staging layer does not produce a second wave of downstream failures. Every
result is logged, passes included.

See docs/phases/05-sql-layer.md
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

import duckdb

from usl import config
from usl.db import row_count
from usl.logging_setup import RunContext, ensure_log_tables, log_check_result
from usl.transform import reference
from usl.transform.checks import (
    INTERMEDIATE_CHECKS,
    MART_CHECKS,
    STAGING_CHECKS,
    Check,
    CheckFailure,
    CheckResult,
)
from usl.weather.schema import ensure_weather_table

log = logging.getLogger(__name__)

# Explicit order rather than an inferred dependency graph. Six models is not
# enough to justify a parser, and an explicit list is something a reader can
# check against the diagram in the README.
MODELS: tuple[str, ...] = (
    "stg_clubs",
    "stg_matches",
    "stg_weather",
    "int_standings",
    "int_stakes",
    "mart_match_features",
    "mart_decay_curve",
)

TIERS: dict[str, str] = {
    "stg_clubs": "staging",
    "stg_matches": "staging",
    "stg_weather": "staging",
    "int_standings": "intermediate",
    "int_stakes": "intermediate",
    "mart_match_features": "mart",
    "mart_decay_curve": "mart",
}

TIER_ORDER: tuple[str, ...] = ("staging", "intermediate", "mart")

TIER_CHECKS: dict[str, tuple[Check, ...]] = {
    "staging": STAGING_CHECKS,
    "intermediate": INTERMEDIATE_CHECKS,
    "mart": MART_CHECKS,
}

_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load_reference_tables(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Materialise the hand-maintained CSVs under usl/ref/ as tables, plus ref_config.

    club_aliases, club_conference, conference_structure, derbies, stadiums.
    These are code, not data: checked into git, reviewed in diffs, and
    load-bearing. Every value is whitespace-normalised on the way in by
    reference.read_reference_csv, the same rule the staging join applies to the
    raw side, so the two cannot drift.

    Args:
        con: Open connection with write access.

    Returns:
        Row count per reference table.
    """
    counts: dict[str, int] = {}
    for name, path in reference.REFERENCE_CSVS.items():
        counts[name] = reference.read_reference_csv(con, name, path)
        log.info("loaded reference %s rows=%s (%s)", name, counts[name], path.name)
    reference.create_ref_config(con)
    return counts


def materialise(con: duckdb.DuckDBPyConnection, model: str) -> int:
    """Materialise one SQL model, replacing any existing table.

    CREATE OR REPLACE TABLE ... AS makes every model below raw idempotent for
    free. It is a full rebuild each time, which at this data size costs nothing.
    Note this is the opposite of the raw_matches strategy, which upserts because
    it must not lose history the source no longer serves. Raw accumulates;
    everything below it is derived and disposable.

    Args:
        con: Open connection with write access.
        model: Model name, matching a file usl/sql/<model>.sql.

    Returns:
        Row count of the resulting table.

    Raises:
        ValueError: The name is not one of MODELS.
    """
    if model not in MODELS or not _PLAIN_NAME.fullmatch(model):
        raise ValueError(f"not a declared model: {model!r} (MODELS = {MODELS})")
    if model == "stg_weather":
        # The one raw table that may never have been written: weather is phase
        # two and off by default. Create it empty so the model always builds
        # and the mart's LEFT JOIN is always well-formed.
        ensure_weather_table(con)
    sql = (config.SQL_DIR / f"{model}.sql").read_text(encoding="utf-8")
    con.execute(f"CREATE OR REPLACE TABLE {model} AS\n{sql.strip().rstrip(';')}")
    n = row_count(con, model)
    log.info("materialised %s rows=%s", model, n)
    return n


def materialise_tier(con: duckdb.DuckDBPyConnection, tier: str) -> dict[str, int]:
    """Materialise every model of one tier, in MODELS order.

    Args:
        con: Open connection with write access.
        tier: 'staging', 'intermediate', or 'mart'.

    Returns:
        Row count per model materialised.
    """
    if tier not in TIER_ORDER:
        raise ValueError(f"unknown tier {tier!r}; expected one of {TIER_ORDER}")
    return {model: materialise(con, model) for model in MODELS if TIERS[model] == tier}


def run_checks(
    con: duckdb.DuckDBPyConnection,
    checks: Sequence[Check],
    ctx: RunContext | None = None,
) -> list[CheckResult]:
    """Run every check and log every result, passes included.

    A check that has passed for six weeks and starts failing is a signal; a
    check that only writes a row when it fails gives you no baseline to notice
    that against.

    Args:
        con: Open connection.
        checks: The checks to run, in order. None is skipped on failure.
        ctx: When given, results are written to check_log under this run as
            well as to the log stream.

    Returns:
        One CheckResult per check, in order.
    """
    results: list[CheckResult] = []
    for check in checks:
        result = check(con)
        if ctx is not None:
            log_check_result(con, ctx, result)
        else:
            log.log(
                logging.INFO if result.passed else logging.ERROR,
                "check %-28s %s  %s",
                result.name,
                "passed" if result.passed else "FAILED",
                json.dumps(result.metadata, default=str, sort_keys=True),
            )
        results.append(result)
    return results


def run_sql_layer(con: duckdb.DuckDBPyConnection, ctx: RunContext | None = None) -> dict[str, int]:
    """Load the reference tables, then materialise each tier and run its checks.

    Collect check failures within a tier and report them together; stop between
    tiers. One run should tell you everything wrong at a level - three unmapped
    clubs and a row-count drop are probably the same root cause, and seeing them
    together tells you that. But computing a mart on staging you already know is
    broken just produces a second wave of downstream artefacts.

    Args:
        con: Open connection with write access.
        ctx: The run context. When given, check results are written to
            check_log (the log tables are created if missing).

    Returns:
        Row count per materialised model, for the run log.

    Raises:
        CheckFailure: When any check in a tier fails. The message names the
            tier, the failed checks, and each one's metadata on one line.
    """
    if ctx is not None:
        ensure_log_tables(con)
    load_reference_tables(con)
    counts: dict[str, int] = {}
    for tier in TIER_ORDER:
        counts.update(materialise_tier(con, tier))
        results = run_checks(con, TIER_CHECKS[tier], ctx)
        failed = [r for r in results if not r.passed]
        if failed:
            names = [r.name for r in failed]
            details = "; ".join(
                f"{r.name}: {json.dumps(r.metadata, default=str, sort_keys=True)}" for r in failed
            )
            raise CheckFailure(f"{len(failed)} check(s) failed in {tier}: {names}; {details}")
    return counts
