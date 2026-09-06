"""Durable archive of raw API responses.

The single most important directory in this project. data/usl.duckdb is a build
product you can delete freely; data/raw_archive/ is the only copy of the source
data that exists once the FootyStats subscription lapses, and no amount of later
work regenerates it.

Consequences that follow from that, and that the implementation must honour:

  - The archive is committed to git. It is not gitignored, unlike the database.
  - A response is written to disk BEFORE anything that could raise touches it.
    A parse bug must not cost you a request.
  - Nothing already archived is overwritten by a body that has not been
    validated. A fresh body lands in a sibling '.partial' file, is checked, and
    only then replaces the archived copy in one atomic rename; a body that
    fails the check is kept beside it as '.bad' for inspection and is never
    served as a hit.
  - A file that is half-written (a crash mid-write) or empty is not a hit
    either, so a spent request whose response never fully landed is requested
    again rather than poisoning every later run.
  - Filenames are readable, not hashes, so the archive can be browsed. A live
    season pulled weekly is archived as one dated snapshot per pull.
  - The API key never appears in a filename or inside an archived file.

See docs/phases/00-data-access-and-the-clock.md, exercise 0.1
"""

from __future__ import annotations

import datetime as dt
import json
import os
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

# A body that has been fetched but not yet validated.
PARTIAL_SUFFIX = ".partial"
# A body that failed validation, kept for inspection, never served.
QUARANTINE_SUFFIX = ".bad"
# The tag that makes one weekly pull of a live season a distinct archive entry.
SNAPSHOT_PREFIX = "as_of_"


class ArchiveError(RuntimeError):
    """An archived file cannot be read as the response it claims to be.

    Names the file, because "which one" is the first question when this fires
    after the subscription has lapsed and nothing can be re-requested.
    """


def _archive_dir() -> Path:
    """config.ARCHIVE_DIR, read at call time so tests can point it elsewhere."""
    return Path(config.ARCHIVE_DIR)


def _safe(text: object) -> str:
    """Render one filename component: str() then everything unsafe to '_'."""
    return _UNSAFE.sub("_", str(text))


def snapshot_tag(as_of: str | dt.date) -> str:
    """The archive tag for one dated pull of a live season, e.g. 'as_of_2026-09-08'.

    Args:
        as_of: The pull date, as a date or an ISO string.

    Returns:
        The tag to pass as archive_path(..., tag=).
    """
    day = as_of.isoformat() if isinstance(as_of, dt.date) else str(as_of)
    return SNAPSHOT_PREFIX + day


def archive_path(endpoint: str, params: dict[str, Any], *, tag: str | None = None) -> Path:
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
        tag: An extra label that is part of the archive identity but not of
            the request - a dated snapshot of a live season. Goes last in the
            name: 'league-matches_season_id_1625_as_of_2026-09-08.json'.

    Returns:
        Path under config.ARCHIVE_DIR.
    """
    parts = [_safe(endpoint)]
    for name in sorted(params):
        if name.lower() == "key":
            continue
        parts.append(_safe(name))
        parts.append(_safe(params[name]))
    if tag:
        parts.append(_safe(tag))
    return _archive_dir() / ("_".join(parts) + ".json")


def _is_usable(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def is_archived(endpoint: str, params: dict[str, Any], *, tag: str | None = None) -> bool:
    """Whether this exact request has already been archived.

    An empty file is not an archive hit: it is what a crash between creating
    the file and writing it leaves behind, and serving it would mean a spent
    request whose response never landed poisons every later run.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.
        tag: The snapshot tag, if any.

    Returns:
        True if a non-empty response is on disk.
    """
    return _is_usable(archive_path(endpoint, params, tag=tag))


def read_file(path: Path) -> dict[str, Any]:
    """Parse one archived file.

    Args:
        path: An archive file.

    Returns:
        The parsed JSON body.

    Raises:
        ArchiveError: The file is not JSON. The message names it and says what
            to do - the client never writes such a file to the archive, so one
            that exists was corrupted after the fact.
    """
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchiveError(
            f"{path} is in the archive but is not JSON ({exc.msg} at char {exc.pos}). "
            f"Move it aside (for example rename it to {path.name}{QUARANTINE_SUFFIX}) and "
            "re-request it with a key; nothing in this project writes a non-JSON body to "
            "the archive, so this file was changed after it was archived."
        ) from None
    return payload


def read_archived(
    endpoint: str, params: dict[str, Any], *, tag: str | None = None
) -> dict[str, Any]:
    """Read a previously archived response.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.
        tag: The snapshot tag, if any.

    Returns:
        The parsed JSON body.

    Raises:
        FileNotFoundError: If nothing usable is archived for this request. The
            message names the path, because "which file did it want" is the
            first question when this fires after the subscription has lapsed.
        ArchiveError: The file is present but is not JSON.
    """
    path = archive_path(endpoint, params, tag=tag)
    if not _is_usable(path):
        raise FileNotFoundError(f"nothing archived for {endpoint} {params}: expected {path}")
    return read_file(path)


def write_partial(
    endpoint: str, params: dict[str, Any], body: str, *, tag: str | None = None
) -> Path:
    """Persist one raw response body beside its archive slot, unvalidated.

    Called with the response text before it is deserialised, so that a
    malformed payload is still on disk to debug against rather than being lost
    with the exception. The body goes to '<archive file>.partial', flushed and
    fsynced, and the archive slot itself is untouched until commit_partial.

    The body is written as its UTF-8 bytes, with no pretty-printing, re-encoding
    or newline translation: what is on disk is what the API said.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.
        body: Raw response text, exactly as received.
        tag: The snapshot tag, if any.

    Returns:
        The '.partial' path written.
    """
    path = archive_path(endpoint, params, tag=tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    with open(partial, "wb") as fh:
        fh.write(body.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    return partial


def commit_partial(partial: Path) -> Path:
    """Make a validated '.partial' body the archived copy, atomically.

    os.replace is atomic on POSIX and on Windows, so a reader sees either the
    previous archived file or the new one, never a half-written mixture, and a
    crash before this call leaves the previous copy exactly as it was.

    Args:
        partial: The path returned by write_partial.

    Returns:
        The archive path now holding the body.
    """
    final = partial.with_name(partial.name.removesuffix(PARTIAL_SUFFIX))
    os.replace(partial, final)
    return final


def quarantine_partial(partial: Path) -> Path:
    """Keep a body that failed validation as '<archive file>.bad', never served.

    Args:
        partial: The path returned by write_partial.

    Returns:
        The '.bad' path.
    """
    bad = partial.with_name(partial.name.removesuffix(PARTIAL_SUFFIX) + QUARANTINE_SUFFIX)
    os.replace(partial, bad)
    return bad


def write_archive(
    endpoint: str, params: dict[str, Any], body: str, *, tag: str | None = None
) -> Path:
    """Persist one raw response body straight into the archive, atomically.

    write_partial followed by commit_partial, for callers that have already
    validated the body or are seeding an archive (the tests, the demos).

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.
        body: Raw response text, exactly as received.
        tag: The snapshot tag, if any.

    Returns:
        The path written.
    """
    return commit_partial(write_partial(endpoint, params, body, tag=tag))


def latest_snapshot(endpoint: str, params: dict[str, Any]) -> Path | None:
    """The newest dated snapshot archived for a request, or None.

    Snapshot tags are ISO dates, so the newest sorts last by name.

    Args:
        endpoint: API endpoint name.
        params: Request parameters, key excluded.

    Returns:
        The path of the newest usable snapshot, or None when there is none.
    """
    stem = archive_path(endpoint, params).stem
    candidates = sorted(
        path for path in _archive_dir().glob(f"{stem}_{SNAPSHOT_PREFIX}*.json") if _is_usable(path)
    )
    return candidates[-1] if candidates else None


def archive_summary() -> dict[str, Any]:
    """Describe what the archive currently holds.

    Worth having as a command you can run during the subscription window to
    answer "what have I not pulled yet", which is the question that matters while
    the clock is running.

    Returns:
        A dict with: files (int), bytes (int), endpoints (endpoint -> file
        count), season_ids (sorted ints parsed from filenames), oldest and
        newest (ISO-8601 UTC mtimes, or None when the archive is empty),
        quarantined (count of '.bad' files, which want a look), and directory
        (the archive path as a string).
    """
    directory = _archive_dir()
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    quarantined = len(list(directory.glob(f"*{QUARANTINE_SUFFIX}"))) if directory.is_dir() else 0

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
        "quarantined": quarantined,
        "directory": str(directory),
    }
