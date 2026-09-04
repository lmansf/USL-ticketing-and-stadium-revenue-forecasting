"""Durable archive of raw API responses.

The single most important directory in this project. data/usl.duckdb is a build
product you can delete freely; data/raw_archive/ is the only copy of the source
data that exists once the FootyStats subscription lapses, and no amount of later
work regenerates it.

Consequences that follow from that, and that the implementation must honour:

  - The archive is committed to git. It is not gitignored, unlike the database.
  - A response is written to disk BEFORE anything that could raise touches it.
    A parse bug must not cost you a request.
  - Filenames are readable, not hashes, so the archive can be browsed.
  - The API key never appears in a filename or inside an archived file.

See docs/phases/00-data-access-and-the-clock.md, exercise 0.1
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def archive_path(endpoint: str, params: dict[str, Any]) -> Path:
    """Build the archive filename for one request.

    Readable, not hashed: 'league-matches_season_1625.json' rather than a digest.
    The archive is meant to be browsable, and the cache key should be obvious on
    sight when you are working out what you did or did not pull.

    Args:
        endpoint: API endpoint name, e.g. 'league-matches'.
        params: Request parameters. The key MUST be excluded before this is
            called, or a paid credential ends up in a committed filename.

    Returns:
        Path under config.ARCHIVE_DIR.

    TODO: implement. Sort the params so the same request always maps to the same
    filename regardless of dict ordering.
    """
    raise NotImplementedError("TODO: see docs/phases/00-data-access-and-the-clock.md")


def is_archived(endpoint: str, params: dict[str, Any]) -> bool:
    """Whether this exact request has already been archived.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.

    Returns:
        True if the response is on disk.

    TODO: implement.
    """
    raise NotImplementedError("TODO")


def read_archived(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Read a previously archived response.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.

    Returns:
        The parsed JSON body.

    Raises:
        FileNotFoundError: If nothing is archived for this request.

    TODO: implement.
    """
    raise NotImplementedError("TODO")


def write_archive(endpoint: str, params: dict[str, Any], body: str) -> Path:
    """Persist one raw response body, unparsed.

    Called with the response text before it is deserialised, so that a malformed
    payload is still on disk to debug against rather than being lost with the
    exception.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.
        body: Raw response text, exactly as received.

    Returns:
        The path written.

    TODO: implement. Create parent directories. Do not pretty-print or re-encode
    the body - archive it byte for byte, so that what you have on disk is what
    the API actually said.
    """
    raise NotImplementedError("TODO: see docs/phases/00-data-access-and-the-clock.md")


def archive_summary() -> dict[str, Any]:
    """Describe what the archive currently holds.

    Worth having as a command you can run during the subscription window to
    answer "what have I not pulled yet", which is the question that matters while
    the clock is running.

    Returns:
        Endpoint counts, season ids covered, total bytes, oldest and newest file.

    TODO: implement.
    """
    raise NotImplementedError("TODO")
