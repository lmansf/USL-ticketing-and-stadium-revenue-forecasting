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

See docs/mvp/05-mvp-schedule.md
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from usl import config

log = logging.getLogger(__name__)


def cmd_backfill(args: argparse.Namespace) -> int:
    """Ingest and load every season in usl/ref/seasons.csv.

    Served from data/raw_archive/ where possible, so re-running costs nothing and
    works with no API key. Log per season as you go - a backfill that fails on
    season seven should tell you it got through six.

    Args:
        args: Parsed arguments, carrying db and force.

    Returns:
        Process exit code. Zero on success.

    TODO: implement. Open the database with db.connect_for_write, ensure the log
    and raw tables, run load.raw.backfill, and record the stage either way.
    """
    raise NotImplementedError("TODO: see docs/phases/01-ingest-to-raw.md")


def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest and load the current season only.

    The weekly delta. Everything already loaded is updated in place rather than
    appended - see load.raw.upsert_matches.

    Note this is the one stage that genuinely needs a live subscription: a new
    week of matches is by definition not in the archive. Once the subscription
    lapses this becomes a no-op against archived data, which is correct but worth
    saying out loud in the run log rather than letting it look like a successful
    refresh.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/01-ingest-to-raw.md")


def cmd_archive(args: argparse.Namespace) -> int:
    """Report what data/raw_archive/ currently holds.

    The question that matters while the subscription clock is running: what have
    I not pulled yet? Worth running daily during the paid month.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.

    TODO: implement over ingest.archive.archive_summary().
    """
    raise NotImplementedError("TODO: see docs/phases/00-data-access-and-the-clock.md")


def cmd_transform(args: argparse.Namespace) -> int:
    """Run the SQL layer: staging, intermediate, mart, with checks between tiers.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code. Non-zero when a check fails, so that the scheduler's
        result code means something rather than being decorative.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/05-sql-layer.md")


def cmd_train(args: argparse.Namespace) -> int:
    """Train both models plus the naive baseline, writing all three output tables.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/07-two-models.md")


def cmd_export(args: argparse.Namespace) -> int:
    """Write Tableau extracts.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/08-tableau.md")


def cmd_weekly(args: argparse.Namespace) -> int:
    """The Tuesday run: ingest, transform, train, export, in order.

    One run_id across all four stages, so "show me every stage of the run that
    failed last Tuesday" is one query rather than a reconstruction from
    timestamps.

    Stop on the first stage that fails. There is no value in training on a mart
    you already know is broken, and doing so produces a second wave of failures
    that are all downstream artefacts of the first.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code. Non-zero on any stage failure or failed check, so the
        scheduler's Last Run Result is meaningful.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/mvp/05-mvp-schedule.md")


COMMANDS = {
    "backfill": cmd_backfill,
    "ingest": cmd_ingest,
    "archive": cmd_archive,
    "transform": cmd_transform,
    "train": cmd_train,
    "export": cmd_export,
    "weekly": cmd_weekly,
}


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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch.

    Args:
        argv: Argument list. Defaults to sys.argv[1:].

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)

    # TODO: replace with logging_setup.configure_logging once implemented.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        return COMMANDS[args.command](args)
    except NotImplementedError as exc:
        log.error("not implemented: %s", exc)
        log.error("This repo ships as stubs. See docs/README.md.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
