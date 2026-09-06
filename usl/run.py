"""Command line entry point.

Everything the scheduler needs is behind one command. A scheduler that knows
about the pipeline's internals is a scheduler you have to keep in sync with it.

    python -m usl.run weekly

Stages can also be run individually while you are building:

    python -m usl.run backfill
    python -m usl.run ingest
    python -m usl.run archive
    python -m usl.run transform
    python -m usl.run train
    python -m usl.run export
    python -m usl.run league-list

Every command that touches the database opens it once, with the lock guard in
usl.db, and runs its body under usl.logging_setup.stage so run_log carries one
row per stage per invocation. The exit code is the scheduler's only view of the
run, so it means something:

    0  every stage finished and every check passed
    1  a stage failed, a check failed, or the request could not be served
    2  a command is not implemented
    3  the database was locked for the whole retry window (scenario D1)

See docs/mvp/05-mvp-schedule.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

from usl import config
from usl.db import DatabaseLockedError, connect_for_write, table_exists
from usl.export.extracts import export_all
from usl.ingest.archive import archive_summary
from usl.ingest.footystats import NoSubscriptionError, list_leagues
from usl.load.raw import backfill, load_season, raw_summary
from usl.logging_setup import (
    LoadStats,
    RunContext,
    configure_logging,
    ensure_log_tables,
    new_run_context,
    stage,
)
from usl.models.train import train_all
from usl.transform.checks import CheckFailure
from usl.transform.runner import run_sql_layer

# Named explicitly: under "python -m usl.run" __name__ is "__main__", which is
# not a name anyone would search the log file for.
log = logging.getLogger("usl.run")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_IMPLEMENTED = 2
EXIT_LOCKED = 3


class ConfigurationError(RuntimeError):
    """The reference data does not let the command run.

    A blank season list, or a current season with no id: things a person fixes
    in usl/ref/seasons.csv, so the message says which file and which row.
    """


# --------------------------------------------------------------------------
# Stage bodies
#
# Each takes an open connection and the run context, fills the run_log metadata
# for its stage, and raises on failure. cmd_* wrap one of them in a connection;
# cmd_weekly runs four of them under one connection and one run_id.
# --------------------------------------------------------------------------


def _open(args: argparse.Namespace) -> Any:
    """The write connection for a command, honouring the hidden lock overrides.

    The overrides exist for demo D1, which wants to show the lock failure in
    seconds rather than sit through the thirty-second production retry window.
    """
    return connect_for_write(
        args.db,
        max_attempts=getattr(args, "lock_attempts", None),
        backoff_base=getattr(args, "lock_backoff", None),
    )


def _freshness(con: duckdb.DuckDBPyConnection, meta: dict[str, Any]) -> None:
    """Copy raw_summary's freshness and quality fields into a stage's metadata.

    Written on every stage that reads raw_matches, so max_match_date and
    null_attendance_pct are on every row of run_log and plot over time.
    """
    summary = raw_summary(con)
    meta["seasons"] = summary["seasons"]
    meta["max_match_date"] = summary["max_match_date"]
    meta["null_attendance_pct"] = summary["null_attendance_pct"]


def _load_split(meta: dict[str, Any], stats: LoadStats) -> None:
    """Record the insert/update/unchanged split of a load in a stage's metadata."""
    meta["rows_read"] = stats.total
    meta["rows_inserted"] = stats.inserted
    meta["rows_updated"] = stats.updated
    meta["rows_unchanged"] = stats.unchanged


def _season_ids() -> list[int]:
    """Season ids from usl/ref/seasons.csv, logging the seasons that have none.

    The blank rows are the "what have I not pulled yet" list while the
    subscription clock runs, so each one is named rather than silently skipped.

    Raises:
        ConfigurationError: No row carries a season_id at all.
    """
    rows = config.read_seasons_csv()
    ids = [row.season_id for row in rows if row.season_id is not None]
    for row in rows:
        if row.season_id is None:
            log.info(
                "season %s has no season_id in %s - not pulled yet (%s)",
                row.season,
                config.SEASONS_CSV.name,
                row.note or "no note",
            )
    if not ids:
        raise ConfigurationError(
            f"no row in {config.SEASONS_CSV} carries a season_id, so there is nothing to "
            "backfill. Run 'python -m usl.run league-list' with a key to find the ids, or "
            f"keep the example row (season_id {config.EXAMPLE_SEASON_ID}) to run from the "
            "archive."
        )
    log.info("backfill: %d season id(s) from %s: %s", len(ids), config.SEASONS_CSV.name, ids)
    return ids


def _current_season_id() -> int:
    """The season_id of config.CURRENT_SEASON, from usl/ref/seasons.csv.

    Raises:
        ConfigurationError: The current season has no row, or a row with no id.
    """
    for row in config.read_seasons_csv():
        if row.season == config.CURRENT_SEASON:
            if row.season_id is None:
                raise ConfigurationError(
                    f"config.CURRENT_SEASON is {config.CURRENT_SEASON} but its row in "
                    f"{config.SEASONS_CSV} has no season_id. Fill it in from "
                    "'python -m usl.run league-list'."
                )
            return row.season_id
    raise ConfigurationError(
        f"config.CURRENT_SEASON is {config.CURRENT_SEASON} but {config.SEASONS_CSV} has no "
        "row for that season. Add one with its season_id."
    )


def _stage_backfill(
    con: duckdb.DuckDBPyConnection, ctx: RunContext, args: argparse.Namespace
) -> None:
    with stage(con, ctx, "backfill") as meta:
        stats = backfill(con, _season_ids())
        _load_split(meta, stats)
        _freshness(con, meta)


def _stage_ingest(
    con: duckdb.DuckDBPyConnection, ctx: RunContext, args: argparse.Namespace
) -> None:
    with stage(con, ctx, "ingest") as meta:
        if config.CURRENT_SEASON is None:
            # Recorded as a success with zero rows, on purpose: the stage ran
            # and refreshed nothing, and the run log should say exactly that
            # rather than look like a refresh that found no new matches.
            log.info("archive-only: no current season configured, nothing to ingest")
            _load_split(meta, LoadStats())
            _freshness(con, meta)
            return
        season_id = _current_season_id()
        if not config.has_subscription():
            log.warning(
                "no FOOTYSTATS_API_KEY is set: the weekly refresh cannot reach the API and "
                "is serving season %s (season_id %s) from the archive. New matches will not "
                "appear until a key is configured.",
                config.CURRENT_SEASON,
                season_id,
            )
        # Each weekly pull is its own dated archive entry, so the season is
        # actually re-requested while the key is live and the newest snapshot
        # is served (with a warning) once it is not. No --force needed.
        stats = load_season(con, season_id, force=args.force, snapshot=ctx.started_at.date())
        _load_split(meta, stats)
        _freshness(con, meta)


def _stage_transform(
    con: duckdb.DuckDBPyConnection, ctx: RunContext, args: argparse.Namespace
) -> None:
    with stage(con, ctx, "transform") as meta:
        if not table_exists(con, "raw_matches"):
            raise ConfigurationError(
                f"{args.db} has no raw_matches table - nothing has been loaded. Run "
                "'python -m usl.run backfill' first (served from data/raw_archive/, no key "
                "needed)."
            )
        _freshness(con, meta)
        meta["rows_read"] = raw_summary(con)["rows"]
        counts = run_sql_layer(con, ctx)
        log.info(
            "transform: %s",
            ", ".join(f"{model}={rows}" for model, rows in counts.items()),
        )


def _stage_train(con: duckdb.DuckDBPyConnection, ctx: RunContext, args: argparse.Namespace) -> None:
    with stage(con, ctx, "train") as meta:
        if not table_exists(con, "mart_match_features"):
            raise ConfigurationError(
                f"{args.db} has no mart_match_features table. Run "
                "'python -m usl.run transform' before training."
            )
        summary = train_all(con, args.run_date, seeds=args.seeds)
        # Freshness and quality fields go on every row of run_log, this one
        # included; rows_read and seasons then describe what training saw.
        _freshness(con, meta)
        meta["rows_read"] = summary["n_played"]
        meta["seasons"] = [
            int(row[0])
            for row in con.execute(
                "SELECT DISTINCT season FROM mart_match_features WHERE season IS NOT NULL "
                "ORDER BY season"
            ).fetchall()
        ]
        log.info(
            "train: MAE %s; split %s; train %d test %d future %d",
            ", ".join(f"{name}={mae:.1f}" for name, mae in summary["mae"].items()),
            summary["split_date"],
            summary["n_train"],
            summary["n_test"],
            summary["n_future"],
        )


def _stage_export(
    con: duckdb.DuckDBPyConnection, ctx: RunContext, args: argparse.Namespace
) -> None:
    with stage(con, ctx, "export") as meta:
        paths = export_all(con, args.out_dir, hyper=args.hyper)
        meta["rows_read"] = sum(_csv_rows(path) for path in paths if path.suffix == ".csv")
        _freshness(con, meta)
        log.info("export: %d file(s), %d rows in total", len(paths), meta["rows_read"])


def _csv_rows(path: Path) -> int:
    """Data rows in a CSV written by the export: lines minus the header."""
    with open(path, encoding="utf-8", newline="") as fh:
        return max(0, sum(1 for _ in fh) - 1)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_backfill(args: argparse.Namespace, ctx: RunContext) -> int:
    """Ingest and load every season in usl/ref/seasons.csv that has an id.

    Served from data/raw_archive/ where possible, so re-running costs nothing and
    works with no API key. The seasons with no id yet are logged one by one -
    that list is the "what have I not pulled" answer while the subscription
    clock is running - and a file with no ids at all is an error, not an empty
    success.

    Args:
        args: Parsed arguments, carrying db and force.
        ctx: The run context.

    Returns:
        Process exit code. Zero on success; failures propagate to main.
    """
    with _open(args) as con:
        ensure_log_tables(con)
        _stage_backfill(con, ctx, args)
    return EXIT_OK


def cmd_ingest(args: argparse.Namespace, ctx: RunContext) -> int:
    """Ingest and load the current season only.

    The weekly delta. Everything already loaded is updated in place rather than
    appended - see load.raw.upsert_matches.

    This is the one stage that genuinely needs a live subscription. The pull is
    archived as a dated snapshot of the season, one per weekly run, so with a
    key it always requests fresh data and never spends a request twice on the
    same day. With config.CURRENT_SEASON unset the data is archive-only and the
    stage records a success with zero rows and says so, rather than looking
    like a refresh. With a current season and no key it serves the newest
    archived snapshot and warns that nothing is being refreshed.

    Args:
        args: Parsed arguments.
        ctx: The run context.

    Returns:
        Process exit code.
    """
    with _open(args) as con:
        ensure_log_tables(con)
        _stage_ingest(con, ctx, args)
    return EXIT_OK


def cmd_archive(args: argparse.Namespace, ctx: RunContext) -> int:
    """Report what data/raw_archive/ currently holds.

    The question that matters while the subscription clock is running: what have
    I not pulled yet? Worth running daily during the paid month. Does not open
    the database.

    Args:
        args: Parsed arguments.
        ctx: The run context.

    Returns:
        Process exit code.
    """
    summary = archive_summary()
    print(f"archive: {summary['directory']}")
    print(f"  files:      {summary['files']}")
    print(f"  bytes:      {summary['bytes']:,}")
    if summary["endpoints"]:
        for endpoint, count in summary["endpoints"].items():
            print(f"  endpoint:   {endpoint} ({count} file(s))")
    else:
        print("  endpoint:   none archived yet")
    ids = summary["season_ids"]
    print(f"  season ids: {', '.join(str(i) for i in ids) if ids else 'none'}")
    print(f"  oldest:     {summary['oldest'] or '-'}")
    print(f"  newest:     {summary['newest'] or '-'}")
    if summary["quarantined"]:
        # A .bad file is a response that failed validation and was kept for a
        # look. It is never served, but it means a request was spent for nothing.
        print(
            f"  QUARANTINED: {summary['quarantined']} .bad file(s) - open them before pulling again"
        )
    not_pulled = [row for row in config.read_seasons_csv() if row.season_id is None]
    if not_pulled:
        print(f"  not pulled yet ({config.SEASONS_CSV.name} rows with no season_id):")
        for row in not_pulled:
            print(f"    {row.season}: {row.note or 'no note'}")
    return EXIT_OK


def cmd_transform(args: argparse.Namespace, ctx: RunContext) -> int:
    """Run the SQL layer: staging, intermediate, mart, with checks between tiers.

    Args:
        args: Parsed arguments.
        ctx: The run context.

    Returns:
        Process exit code. Non-zero when a check fails, so that the scheduler's
        result code means something rather than being decorative.
    """
    with _open(args) as con:
        ensure_log_tables(con)
        _stage_transform(con, ctx, args)
    return EXIT_OK


def cmd_train(args: argparse.Namespace, ctx: RunContext) -> int:
    """Train both models plus the naive baseline, writing all five output tables.

    Args:
        args: Parsed arguments, carrying run_date and seeds.
        ctx: The run context.

    Returns:
        Process exit code.
    """
    with _open(args) as con:
        ensure_log_tables(con)
        _stage_train(con, ctx, args)
    return EXIT_OK


def cmd_export(args: argparse.Namespace, ctx: RunContext) -> int:
    """Write Tableau extracts.

    Args:
        args: Parsed arguments, carrying out_dir and hyper.
        ctx: The run context.

    Returns:
        Process exit code.
    """
    with _open(args) as con:
        ensure_log_tables(con)
        _stage_export(con, ctx, args)
    return EXIT_OK


def cmd_weekly(args: argparse.Namespace, ctx: RunContext) -> int:
    """The Tuesday run: ingest, transform, train, export, in order.

    One run_id and one connection across all four stages, so "show me every
    stage of the run that failed last Tuesday" is one query rather than a
    reconstruction from timestamps.

    Stops on the first stage that fails: the stage manager records the failure
    and re-raises, and main turns that into the exit code. There is no value in
    training on a mart you already know is broken, and doing so produces a
    second wave of failures that are all downstream artefacts of the first.

    Args:
        args: Parsed arguments.
        ctx: The run context.

    Returns:
        Process exit code. Non-zero on any stage failure or failed check, so the
        scheduler's Last Run Result is meaningful.
    """
    with _open(args) as con:
        ensure_log_tables(con)
        _stage_ingest(con, ctx, args)
        _stage_transform(con, ctx, args)
        _stage_train(con, ctx, args)
        _stage_export(con, ctx, args)
    return EXIT_OK


def cmd_league_list(args: argparse.Namespace, ctx: RunContext) -> int:
    """Print every league-season the key can see, with its season id.

    This is how usl/ref/seasons.csv gets filled in: you cannot request a year,
    only a season id. Served from the archive when the response is there;
    otherwise it needs a key, and says so. Does not open the database.

    Args:
        args: Parsed arguments, carrying force.
        ctx: The run context.

    Returns:
        Process exit code. 1 when there is no key and no archived response.
    """
    try:
        leagues = list_leagues(force=args.force)
    except NoSubscriptionError as exc:
        print(f"league-list needs a key: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if leagues.empty:
        print("league-list: the response carried no leagues")
        return EXIT_OK
    print(leagues.to_string(index=False))
    print(f"{len(leagues)} league-season row(s)")
    return EXIT_OK


Command = Callable[[argparse.Namespace, RunContext], int]

COMMANDS: dict[str, Command] = {
    "backfill": cmd_backfill,
    "ingest": cmd_ingest,
    "archive": cmd_archive,
    "transform": cmd_transform,
    "train": cmd_train,
    "export": cmd_export,
    "weekly": cmd_weekly,
    "league-list": cmd_league_list,
}


# --------------------------------------------------------------------------
# Argument parsing and dispatch
# --------------------------------------------------------------------------


def _iso_date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date (YYYY-MM-DD): {text!r}") from exc


def _seed_list(text: str) -> tuple[int, ...]:
    try:
        seeds = tuple(int(part) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"seeds must be comma-separated integers: {text!r}"
        ) from exc
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    return seeds


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="usl",
        description="USL Championship attendance forecasting pipeline.",
    )
    parser.add_argument(
        "command",
        choices=sorted(COMMANDS),
        help="Pipeline stage to run. 'weekly' runs the whole thing.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=config.DB_PATH,
        help=f"Path to the DuckDB file (default: {config.DB_PATH}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-request even if the response is already archived. Spends a request "
            "against the subscription - only useful for correcting an archived response."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="DEBUG logging: per-request detail, cache paths, SQL executed.",
    )
    parser.add_argument(
        "--run-date",
        type=_iso_date,
        default=None,
        help="train: the run_date stamped on the model tables (ISO date, default today).",
    )
    parser.add_argument(
        "--seeds",
        type=_seed_list,
        default=None,
        help=(
            "train: comma-separated seeds for the variance estimate; the first is the "
            f"primary (default: {','.join(str(s) for s in config.VARIANCE_SEEDS)})."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=f"export: where the extracts go (default: {config.EXTRACT_DIR}).",
    )
    parser.add_argument(
        "--hyper",
        action="store_true",
        help="export: also write a .hyper beside each CSV (needs pantab).",
    )
    # Hidden: demo D1 shows the lock failure in seconds rather than sitting
    # through the production retry window. Not for the scheduler.
    parser.add_argument("--lock-attempts", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--lock-backoff", type=float, default=None, help=argparse.SUPPRESS)
    return parser


def _exit_code(exc: Exception, ctx: RunContext, command: str) -> int:
    """Map an exception to the exit code, logging the one-line cause.

    A stage failure has already been logged in full by the stage manager, so
    only the stage name is repeated here; anything raised outside a stage is
    logged with its message. The traceback goes to DEBUG either way.
    """
    if isinstance(exc, DatabaseLockedError):
        log.error("%s", exc)
        log.debug("traceback:", exc_info=True)
        return EXIT_LOCKED
    if isinstance(exc, NotImplementedError):
        log.error("not implemented: %s", exc)
        return EXIT_NOT_IMPLEMENTED
    failed = [name for name, info in ctx.stages.items() if info.get("status") == "failed"]
    if failed:
        log.error("%s: stage %s failed - exit code %d", command, failed[-1], EXIT_FAILED)
    elif isinstance(exc, CheckFailure):
        log.error("%s: checks failed - %s", command, exc)
    else:
        log.error("%s failed: %s: %s", command, type(exc).__name__, exc)
    log.debug("traceback:", exc_info=True)
    return EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, configure logging, dispatch, and map failures to exit codes.

    Args:
        argv: Argument list. Defaults to sys.argv[1:].

    Returns:
        Process exit code: 0 success, 1 a stage or check failed, 2 not
        implemented, 3 the database stayed locked.
    """
    args = build_parser().parse_args(argv)
    ctx = new_run_context()
    configure_logging(logging.DEBUG if args.verbose else logging.INFO, run_id=ctx.run_id)
    log.info("run %s: %s (git %s, db %s)", ctx.run_id[:8], args.command, ctx.git_sha, args.db)

    try:
        code = COMMANDS[args.command](args, ctx)
    except Exception as exc:
        return _exit_code(exc, ctx, args.command)
    if code == EXIT_OK:
        log.info("run %s: %s finished - exit code 0", ctx.run_id[:8], args.command)
    return code


if __name__ == "__main__":
    sys.exit(main())
