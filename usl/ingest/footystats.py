"""FootyStats API client.

JSON over HTTP, authenticated by an API key passed as a query parameter. You
address a season id, not a year - the mapping lives in usl/ref/seasons.csv.

Two properties this client must have, both of which come from the subscription
being a 30-day clock:

  1. Every response is archived to disk before it is parsed.
  2. With no key set, it serves everything from the archive and still works.

Property 2 is what makes the finished repo runnable by someone who has never
paid FootyStats anything - which is most people who will look at it.

See docs/phases/01-ingest-to-raw.md
"""

from __future__ import annotations

from typing import Any

import pandas as pd

BASE_URL = "https://api.football-data-api.com"

# Endpoints used by this project. Confirm the paths against a live response
# before relying on them - the match-detail endpoint in particular is
# undocumented, so its path and field set are things you verify, not assume.
ENDPOINT_LEAGUE_LIST = "league-list"
ENDPOINT_LEAGUE_MATCHES = "league-matches"
ENDPOINT_LEAGUE_TABLE = "league-tables"
ENDPOINT_MATCH_DETAIL = "match"

# Fields the parser requires. Illustrative - confirm against a real response.
REQUIRED_MATCH_FIELDS: frozenset[str] = frozenset(
    {"id", "date_unix", "homeID", "awayID", "homeGoalCount", "awayGoalCount"}
)


class FootyStatsError(RuntimeError):
    """A request failed in a way the pipeline should surface, not swallow.

    The message must carry the endpoint and the HTTP status, and must NOT carry
    the request URL, because the key is in it.
    """


class NoSubscriptionError(FootyStatsError):
    """A request was needed that is not archived, with no API key available.

    This is the one genuinely bad outcome of the 30-day window: a code path
    asking for something nobody pulled, discovered after access has gone. A
    named exception makes it legible instead of surfacing as a confusing 401.
    """


class SchemaDriftError(RuntimeError):
    """The response no longer carries the fields the parser requires.

    Names what was found as well as what was missing. Especially important for
    the undocumented match-detail endpoint, which carries no versioning promise
    and will not announce a change.
    """


def get(endpoint: str, **params: Any) -> dict[str, Any]:
    """Fetch one endpoint, serving from the archive when possible.

    The archive check comes first and does two jobs. During the subscription it
    stops you spending a request twice. After the subscription it is the only
    path, and the pipeline keeps running against a dead key.

    Args:
        endpoint: Endpoint name, e.g. 'league-matches'.
        **params: Query parameters. Do NOT pass the key - this function adds it.

    Returns:
        The parsed JSON body.

    Raises:
        NoSubscriptionError: Not archived, and no key available to fetch it.
        FootyStatsError: The request failed.

    TODO: implement. See docs/phases/01-ingest-to-raw.md, exercise 1.3. Archive
    the body before deserialising it - a JSONDecodeError must not cost a request.
    """
    raise NotImplementedError("TODO: see docs/phases/01-ingest-to-raw.md, exercise 1.3")


def _get_with_retry(endpoint: str, params: dict[str, Any]) -> str:
    """GET an endpoint, retrying transient failures only.

    Transient means connection resets and 5xx. A 401 means the key is wrong or
    the subscription lapsed; a 404 means the endpoint or season id is wrong.
    Neither improves with waiting, and retrying them burns backoff time to
    deliver the same answer.

    Args:
        endpoint: Endpoint name.
        params: Query parameters including the key.

    Returns:
        Raw response text.

    Raises:
        FootyStatsError: Non-transient failure, or attempts exhausted. The
            message names the endpoint and status but never the URL.

    TODO: implement using config.FETCH_MAX_ATTEMPTS and
    config.FETCH_BACKOFF_BASE_SECONDS. Never log the full URL at any level.
    """
    raise NotImplementedError("TODO")


def list_leagues() -> pd.DataFrame:
    """Fetch the league list, to find USL Championship and its season ids.

    Run once. You cannot request a year - you request a season id, so this is how
    the mapping in usl/ref/seasons.csv gets built.

    Returns:
        One row per league-season the subscription covers.

    TODO: implement. Note the entry tier covers a limited number of leagues that
    you select, so USL Championship being absent here means a selection problem,
    not a coverage problem. See docs/reference/open-questions.md.
    """
    raise NotImplementedError("TODO: see docs/phases/00-data-access-and-the-clock.md")


def fetch_season_matches(season_id: int) -> dict[str, Any]:
    """Fetch one season of matches.

    The backbone request. One call per season.

    Args:
        season_id: FootyStats season id, from usl/ref/seasons.csv.

    Returns:
        The raw JSON body.

    TODO: implement over get().
    """
    raise NotImplementedError("TODO")


def fetch_match_detail(match_id: int) -> dict[str, Any]:
    """Fetch one match's detail record.

    UNDOCUMENTED ENDPOINT. It answers, but it carries no contract: no versioning
    promise, no deprecation notice, and no guarantee the field set is the same
    for USL as for the major European leagues.

    That is a reason to archive its responses aggressively rather than a reason
    to avoid it. Only reach for this if per-match attendance is not on the
    league-matches response - see the open question in phase 00.

    Args:
        match_id: FootyStats match id.

    Returns:
        The raw JSON body.

    TODO: implement over get(). Confirm the parameter name against a real call.
    """
    raise NotImplementedError("TODO: see docs/phases/00-data-access-and-the-clock.md")


def fetch_league_table(season_id: int) -> dict[str, Any]:
    """Fetch the published final table for a season.

    A cross-check for phase 04, not a data source. Point-in-time standings still
    have to be reconstructed from results - this endpoint only knows the table
    now, not the table as of some Tuesday in June 2019. But comparing your
    reconstruction's final matchday against this turns phase 04 from "I think
    this is right" into "this matches the published table".

    Args:
        season_id: FootyStats season id.

    Returns:
        The raw JSON body.

    TODO: implement over get(). Cheap, and worth pulling for every season while
    you have access.
    """
    raise NotImplementedError("TODO: see docs/phases/04-standings-as-of-match-date.md")


def parse_season_matches(payload: dict[str, Any], season_id: int) -> pd.DataFrame:
    """Parse a league-matches response into a raw match frame.

    Validates by field NAME. JSON removes the positional-column trap but not the
    drift problem - an undocumented endpoint can change its field set with no
    changelog to consult.

    Missing required fields raise. Extra fields are kept, not dropped: FootyStats
    sends dozens per match, storage is free, and a field you discarded inside the
    30-day window is a field you cannot get back outside it. Filter at staging.

    Args:
        payload: The raw JSON body.
        season_id: Stamped onto every row.

    Returns:
        One row per match as returned, plus season_id, ingested_at, and
        source_endpoint. No type coercion.

    Raises:
        SchemaDriftError: A required field is absent.

    TODO: implement. See docs/phases/01-ingest-to-raw.md, exercise 1.1.
    """
    raise NotImplementedError("TODO: see docs/phases/01-ingest-to-raw.md, exercise 1.1")


def add_match_id(df: pd.DataFrame) -> pd.DataFrame:
    """Add a namespaced match_id from the provider's own id.

    'fs:' + the FootyStats id. Using a provider id rather than hashing
    season|date|home|away is a real improvement: the hash moved whenever a club
    was renamed, which silently turned updates into inserts. A provider id does
    not move when a club rebrands.

    The namespace prefix is there so a second source - the attendance scraper, if
    it turns out to be needed - gets its own space rather than colliding, and so
    an id in a log line says where it came from.

    Args:
        df: Parsed matches carrying the provider id.

    Returns:
        The same frame with match_id added.

    TODO: implement. See docs/phases/01-ingest-to-raw.md, exercise 1.2.
    """
    raise NotImplementedError("TODO: see docs/phases/01-ingest-to-raw.md, exercise 1.2")
