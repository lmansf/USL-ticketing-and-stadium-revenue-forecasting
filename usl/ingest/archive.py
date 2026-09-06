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

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from usl import config

# Anything outside this set in a rendered parameter becomes an underscore, so a
# value like "2018/2019" cannot turn into a directory and a stray space cannot
# make two filenames that look identical in a listing.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# Filenames carry the season id as "season_id_<n>"; archive_summary reads it back.
_SEASON_ID = re.compile(r"season_id_(\d+)")


def _archive_dir() -> Path:
    """config.ARCHIVE_DIR, read at call time so tests can point it elsewhere."""
    return Path(config.ARCHIVE_DIR)


def _safe(text: object) -> str:
    """Render one filename component: str() then everything unsafe to '_'."""
    return _UNSAFE.sub("_", str(text))


def archive_path(endpoint: str, params: dict[str, Any]) -> Path:
    """Build the archive filename for one request.

    Readable, not hashed: 'league-matches_season_id_1625.json' rather than a
    digest. The archive is meant to be browsable, and the cache key should be
    obvious on sight when you are working out what you did or did not pull.

    Params are sorted by name so the same request maps to the same file however
    the caller ordered its dict. The key is dropped here as well as by the
    client, because this is the last line before a paid credential lands in a
    committed filename.

    Args:
        endpoint: API endpoint name, e.g. 'league-matches'.
        params: Request parameters. A 'key' entry (any case) is ignored.

    Returns:
        Path under config.ARCHIVE_DIR.
    """
    parts = [_safe(endpoint)]
    for name in sorted(params):
        if name.lower() == "key":
            continue
        parts.append(_safe(name))
        parts.append(_safe(params[name]))
    return _archive_dir() / ("_".join(parts) + ".json")


def is_archived(endpoint: str, params: dict[str, Any]) -> bool:
    """Whether this exact request has already been archived.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.

    Returns:
        True if the response is on disk.
    """
    return archive_path(endpoint, params).is_file()


def read_archived(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Read a previously archived response.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.

    Returns:
        The parsed JSON body.

    Raises:
        FileNotFoundError: If nothing is archived for this request. The message
            names the path, because "which file did it want" is the first
            question when this fires after the subscription has lapsed.
    """
    path = archive_path(endpoint, params)
    if not path.is_file():
        raise FileNotFoundError(f"nothing archived for {endpoint} {params}: expected {path}")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def write_archive(endpoint: str, params: dict[str, Any], body: str) -> Path:
    """Persist one raw response body, unparsed.

    Called with the response text before it is deserialised, so that a malformed
    payload is still on disk to debug against rather than being lost with the
    exception.

    The body is written as its UTF-8 bytes, with no pretty-printing, re-encoding
    or newline translation: what is on disk is what the API said.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.
        body: Raw response text, exactly as received.

    Returns:
        The path written.
    """
    path = archive_path(endpoint, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8"))
    return path


def archive_summary() -> dict[str, Any]:
    """Describe what the archive currently holds.

    Worth having as a command you can run during the subscription window to
    answer "what have I not pulled yet", which is the question that matters while
    the clock is running.

    Returns:
        A dict with: files (int), bytes (int), endpoints (endpoint -> file
        count), season_ids (sorted ints parsed from filenames), oldest and
        newest (ISO-8601 UTC mtimes, or None when the archive is empty), and
        directory (the archive path as a string).
    """
    directory = _archive_dir()
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []

    endpoints: dict[str, int] = {}
    season_ids: set[int] = set()
    total_bytes = 0
    mtimes: list[float] = []
    for path in files:
        stat = path.stat()
        total_bytes += stat.st_size
        mtimes.append(stat.st_mtime)
        # The endpoint is everything before the first parameter; endpoint names
        # use hyphens, parameters use underscores, so the first '_' is the seam.
        endpoint = path.stem.split("_", 1)[0]
        endpoints[endpoint] = endpoints.get(endpoint, 0) + 1
        for found in _SEASON_ID.findall(path.stem):
            season_ids.add(int(found))

    def _iso(stamp: float) -> str:
        return dt.datetime.fromtimestamp(stamp, tz=dt.UTC).isoformat(timespec="seconds")

    return {
        "files": len(files),
        "bytes": total_bytes,
        "endpoints": dict(sorted(endpoints.items())),
        "season_ids": sorted(season_ids),
        "oldest": _iso(min(mtimes)) if mtimes else None,
        "newest": _iso(max(mtimes)) if mtimes else None,
        "directory": str(directory),
    }
