"""HTTP fetch with retry and a disk cache.

Be polite. Nine seasons is a one-time backfill of a few thousand rows. Sleep
between requests, set a real User-Agent, and cache responses to disk during
development so you are not re-hitting the site every time you fix a parse bug.

See docs/phases/01-scrape-to-raw.md
"""

from __future__ import annotations

from pathlib import Path


class FetchError(RuntimeError):
    """A request failed in a way the pipeline should surface, not swallow.

    The message must carry the HTTP status and the URL. Demo scenario D2 points a
    season URL at a dead path and expects exactly that to appear as a failed
    stage rather than as an empty DataFrame and a green run.
    """


def season_url(season: int) -> str:
    """Build the source URL for one season's match list.

    TODO: implement against the live site. Open the page and read it first - do
    not trust a URL shape written in this repo or generated for you.

    Args:
        season: Four-digit season year.

    Returns:
        Absolute URL.
    """
    raise NotImplementedError("TODO: verify the URL shape on the live site")


def cache_path(season: int) -> Path:
    """Disk cache location for one season's HTML.

    Args:
        season: Four-digit season year.

    Returns:
        Path under config.CACHE_DIR. The directory is gitignored; fixtures you
        deliberately keep for tests belong in demo/fixtures/ instead.

    TODO: implement.
    """
    raise NotImplementedError("TODO")


def fetch_season_html(season: int, *, force: bool = False) -> str:
    """Fetch one season's page, using the disk cache when it is valid.

    Cache validity is not a timestamp. A completed season never changes, so its
    entry never expires; the season currently in progress changes weekly, so it
    is always re-fetched. See exercise 1.3.

    Args:
        season: Four-digit season year.
        force: Re-fetch even for a completed season. Needed when the source
            corrects a historical figure.

    Returns:
        The page HTML.

    Raises:
        FetchError: On a non-transient failure. A 404 is not transient and must
            not be retried into silence.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/01-scrape-to-raw.md, exercise 1.3")


def _get_with_retry(url: str) -> str:
    """GET a URL, retrying transient failures only.

    Transient means connection resets and 5xx. A 404 means the URL is wrong and
    retrying it wastes five minutes of backoff before delivering the same answer.

    Args:
        url: Absolute URL.

    Returns:
        Response body as text.

    Raises:
        FetchError: On a non-transient failure, or after exhausting attempts. The
            message names the status and the URL.

    TODO: implement using config.FETCH_MAX_ATTEMPTS and
    config.FETCH_BACKOFF_BASE_SECONDS. Set config.USER_AGENT on every request.
    """
    raise NotImplementedError("TODO")
