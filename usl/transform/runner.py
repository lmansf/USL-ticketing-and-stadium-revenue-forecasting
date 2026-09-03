"""Execute the SQL layer in dependency order.

Three tiers, kept genuinely separate. Raw is a cache of the internet; staging is
where cleaning is reviewable in a diff; the mart is the only thing the model sees.

See docs/phases/05-sql-layer.md
"""

from __future__ import annotations

import duckdb

# Explicit order rather than an inferred dependency graph. Four models is not
# enough to justify a parser, and an explicit list is something a reader can
# check against the diagram in the README.
MODELS: tuple[str, ...] = (
    "stg_clubs",
    "stg_matches",
    "int_standings",
    "mart_match_features",
)


def load_reference_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Register the hand-maintained CSVs under usl/ref/ as tables.

    club_aliases, club_conference, stadiums, and derbies. These are code, not
    data: checked into git, reviewed in diffs, and load-bearing.

    Args:
        con: Open connection with write access.

    TODO: implement. Apply the same name normalisation here as in the join, so
    the two cannot drift - see docs/phases/03-club-name-consistency.md,
    exercise 3.2.
    """
    raise NotImplementedError("TODO: see docs/phases/03-club-name-consistency.md")


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

    TODO: implement. See docs/phases/05-sql-layer.md, exercise 5.1.
    """
    raise NotImplementedError("TODO: see docs/phases/05-sql-layer.md, exercise 5.1")


def run_sql_layer(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Materialise every model in order, running each tier's checks after it.

    Collect check failures within a tier and report them together; stop between
    tiers. One run should tell you everything wrong at a level - three unmapped
    clubs and a row-count drop are probably the same root cause, and seeing them
    together tells you that. But computing a mart on staging you already know is
    broken just produces a second wave of downstream artefacts.

    Args:
        con: Open connection with write access.

    Returns:
        Row count per materialised model, for the run log.

    Raises:
        CheckFailure: When any check in a tier fails.

    TODO: implement. See docs/phases/05-sql-layer.md, exercise 5.2.
    """
    raise NotImplementedError("TODO: see docs/phases/05-sql-layer.md, exercise 5.2")
