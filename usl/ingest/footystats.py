"""FootyStats API client.

JSON over HTTP, authenticated by an API key passed as a query parameter. You
address a season id, not a year - the mapping lives in usl/ref/seasons.csv.

Two properties this client must have, both of which come from the subscription
being a 30-day clock:

  1. Every response is archived to disk before it is parsed.
  2. With no key set, it serves everything from the archive and still works.

Property 2 is what makes the finished repo runnable by someone who has never
paid FootyStats anything - which is most people who will look at it.

One rule runs through every function here: the key is in every request URL, so
nothing in this module logs a URL, includes a URL in an exception, or chains a
requests exception (whose text carries the URL) onto one of its own.

See docs/phases/01-ingest-to-raw.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import requests

from usl import config
from usl.ingest import archive
from usl.logging_setup import utcnow

log = logging.getLogger(__name__)

BASE_URL = "https://api.football-data-api.com"

# Endpoints used by this project. Confirm the paths against a live response
# before relying on them - the match-detail endpoint in particular is
# undocumented, so its path and field set are things you verify, not assume.
ENDPOINT_LEAGUE_LIST = "league-list"
ENDPOINT_LEAGUE_MATCHES = "league-matches"
ENDPOINT_LEAGUE_TABLE = "league-tables"
ENDPOINT_MATCH_DETAIL = "match"

# Fields the parser requires. Confirmed against the archived example-key
# response (EPL 2018/19, season id 1625), which carries all of them on every
# match record.
REQUIRED_MATCH_FIELDS: frozenset[str] = frozenset(
    {"id", "date_unix", "homeID", "awayID", "homeGoalCount", "awayGoalCount"}
)

# The natural-key fallback for match_id, for frames with no provider id: a
# second source, or the hand-built test fixtures. See add_match_id.
NATURAL_KEY_COLUMNS: tuple[str, ...] = ("season", "date", "home_raw", "away_raw")

# Sleep is a module attribute so tests can replace it and a retry test does not
# actually wait. Both the throttle and the backoff go through it.
_sleep: Callable[[float], None] = time.sleep


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


def get(endpoint: str, *, force: bool = False, **params: Any) -> dict[str, Any]:
    """Fetch one endpoint, serving from the archive when possible.

    The archive check comes first and does two jobs. During the subscription it
    stops you spending a request twice. After the subscription it is the only
    path, and the pipeline keeps running against a dead key.

    Order of operations on a miss: throttle, fetch, write the body to the
    archive, and only then parse it - a JSONDecodeError still leaves the
    response on disk. A body that parses but says success=false is removed
    from the archive again, because an error payload served as a hit forever
    would be worse than no file at all.

    Args:
        endpoint: Endpoint name, e.g. 'league-matches'.
        force: Re-request even when the response is archived. Spends a
            request; only useful for correcting an archived response.
        **params: Query parameters. Do NOT pass the key - this function adds
            it. A 'key' parameter passed anyway is dropped, not used.

    Returns:
        The parsed JSON body.

    Raises:
        NoSubscriptionError: Not archived, and no key available to fetch it.
        FootyStatsError: The request failed, or the API reported failure.
        json.JSONDecodeError: The body is not JSON. It is on disk by then.
    """
    clean = {name: value for name, value in params.items() if name.lower() != "key"}
    if len(clean) != len(params):
        log.warning("%s: a 'key' parameter was passed to get() and ignored", endpoint)
    path = archive.archive_path(endpoint, clean)

    if not force and archive.is_archived(endpoint, clean):
        log.info("archive hit %s", path.name)
        return archive.read_archived(endpoint, clean)

    key = config.FOOTYSTATS_API_KEY
    if not config.has_subscription():
        raise NoSubscriptionError(
            f"{endpoint} {clean} is not under data/raw_archive/ and no FOOTYSTATS_API_KEY "
            "is set. Every response this project needs should be archived - if this fires "
            "after the subscription window, something was never pulled while access was "
            "live."
        )

    log.info("archive miss %s - requesting %s %s", path.name, endpoint, clean)
    _throttle()
    body = _get_with_retry(endpoint, {**clean, "key": key})
    path = archive.write_archive(endpoint, clean, body)
    log.info("%s %s: archived %s (%d bytes)", endpoint, clean, path.name, len(body.encode("utf-8")))

    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError:
        log.error("%s %s: response is not JSON; the body is kept at %s", endpoint, clean, path)
        raise

    if isinstance(payload, dict) and "success" in payload and not payload["success"]:
        path.unlink(missing_ok=True)
        message = str(payload.get("message", "")).replace(key, "***")
        raise FootyStatsError(
            f"{endpoint} {clean}: the API reported failure: {message!r}. "
            "The response was not kept in the archive."
        )
    return payload


def _throttle() -> None:
    """Wait config.REQUEST_DELAY_SECONDS between live requests."""
    delay = config.REQUEST_DELAY_SECONDS
    if delay > 0:
        _sleep(delay)


def _describe_http_failure(endpoint: str, status: int) -> str:
    """The message for a non-retried HTTP status: endpoint and status, no URL."""
    text = f"{endpoint} returned HTTP {status}"
    if status == 401:
        text += " - the key is wrong or the subscription has lapsed"
    elif status == 403:
        text += " - the key does not cover this request, or a proxy is blocking the host"
    elif status == 404:
        text += " - the endpoint path or the season id is wrong"
    elif status == 429:
        text += " - the hourly request limit is used up; wait for it to reset"
    return text + ". Not retried: it would not improve with waiting."


def _get_with_retry(endpoint: str, params: dict[str, Any]) -> str:
    """GET an endpoint, retrying transient failures only.

    Transient means connection errors, timeouts, and 5xx. A 401 means the key
    is wrong or the subscription lapsed; a 404 means the endpoint or season id
    is wrong. Neither improves with waiting, and retrying them burns backoff
    time to deliver the same answer.

    Nothing from the requests layer reaches a log line or an exception message:
    str() of a requests exception and response.url both contain the key. Only
    the exception class name and the HTTP status are reported, and the raised
    FootyStatsError is deliberately not chained onto the original, because a
    logged traceback would print the chained exception's text.

    Args:
        endpoint: Endpoint name.
        params: Query parameters including the key.

    Returns:
        Raw response text.

    Raises:
        FootyStatsError: Non-transient failure, or attempts exhausted. The
            message names the endpoint and status but never the URL.
    """
    attempts = max(1, config.FETCH_MAX_ATTEMPTS)
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
        except (requests.ConnectionError, requests.Timeout) as exc:
            reason = type(exc).__name__
        except requests.RequestException as exc:
            raise FootyStatsError(
                f"{endpoint} request failed with {type(exc).__name__}; not retried"
            ) from None
        else:
            status = int(response.status_code)
            if status < 400:
                body = response.text
                log.info(
                    "%s: HTTP %s, %d bytes (attempt %d/%d)",
                    endpoint,
                    status,
                    len(body.encode("utf-8")),
                    attempt,
                    attempts,
                )
                return body
            if status < 500:
                raise FootyStatsError(_describe_http_failure(endpoint, status))
            reason = f"HTTP {status}"

        if attempt == attempts:
            raise FootyStatsError(
                f"{endpoint} failed after {attempts} attempt(s); last failure: {reason}"
            ) from None
        delay = config.FETCH_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
        log.warning(
            "%s: %s - attempt %d/%d, retrying in %.0fs", endpoint, reason, attempt, attempts, delay
        )
        _sleep(delay)

    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


def list_leagues(*, force: bool = False) -> pd.DataFrame:
    """Fetch the league list, to find USL Championship and its season ids.

    Run once. You cannot request a year - you request a season id, so this is how
    the mapping in usl/ref/seasons.csv gets built.

    The entry tier covers a limited number of leagues that you select in the
    FootyStats account settings, so USL Championship being absent here means a
    selection problem, not a coverage problem.

    Args:
        force: Re-request even when archived.

    Returns:
        One row per league-season: name, league_name, country, season (the
        API's year field), season_id. A league whose record carries no season
        list gets one row with season and season_id None, so it is still
        visible rather than silently dropped.

    Raises:
        SchemaDriftError: The response has no 'data' list.
    """
    payload = get(ENDPOINT_LEAGUE_LIST, force=force)
    records = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        found = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise SchemaDriftError(f"league-list: expected a 'data' list; found {found}")

    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        base = {
            "name": record.get("name"),
            "league_name": record.get("league_name"),
            "country": record.get("country"),
        }
        seasons = record.get("season")
        if isinstance(seasons, dict):
            seasons = [seasons]
        if isinstance(seasons, list) and seasons:
            for season in seasons:
                if isinstance(season, dict):
                    rows.append(
                        {**base, "season": season.get("year"), "season_id": season.get("id")}
                    )
                else:
                    rows.append({**base, "season": None, "season_id": season})
        else:
            rows.append({**base, "season": None, "season_id": None})

    frame = pd.DataFrame(rows, columns=["name", "league_name", "country", "season", "season_id"])
    log.info("league-list: %d league(s), %d league-season row(s)", len(records), len(frame))
    return frame


def fetch_season_matches(season_id: int, *, force: bool = False) -> dict[str, Any]:
    """Fetch one season of matches.

    The backbone request. One call per season, plus one per extra page when the
    API pages the season - every page is archived on its own and the returned
    payload carries the concatenated 'data'. The example season fits in one
    page (380 matches against a page size of 600).

    Args:
        season_id: FootyStats season id, from usl/ref/seasons.csv.
        force: Re-request even when archived.

    Returns:
        The raw JSON body, with 'data' spanning every page.
    """
    payload = get(ENDPOINT_LEAGUE_MATCHES, force=force, season_id=season_id)
    pager = payload.get("pager") if isinstance(payload, dict) else None
    if not isinstance(pager, dict):
        return payload
    try:
        max_page = int(pager.get("max_page") or 1)
    except (TypeError, ValueError):
        max_page = 1
    if max_page <= 1:
        return payload

    log.info("season_id %s: %d pages, fetching the rest", season_id, max_page)
    data = list(payload.get("data") or [])
    for page in range(2, max_page + 1):
        extra = get(ENDPOINT_LEAGUE_MATCHES, force=force, season_id=season_id, page=page)
        data.extend(extra.get("data") or [])

    reported = pager.get("total_results")
    if reported is not None and reported != len(data):
        log.warning(
            "season_id %s: pager reported %s results, %d received across %d pages",
            season_id,
            reported,
            len(data),
            max_page,
        )
    merged = dict(payload)
    merged["data"] = data
    merged["pager"] = {
        **pager,
        "current_page": 1,
        "results_per_page": len(data),
        "total_results": len(data),
    }
    return merged


def fetch_match_detail(match_id: int, *, force: bool = False) -> dict[str, Any]:
    """Fetch one match's detail record.

    UNDOCUMENTED ENDPOINT. It answers, but it carries no contract: no versioning
    promise, no deprecation notice, and no guarantee the field set is the same
    for USL as for the major European leagues.

    That is a reason to archive its responses aggressively rather than a reason
    to avoid it. Only reach for this if per-match attendance is not on the
    league-matches response - see the open question in phase 00. For the
    example season it is on league-matches, populated on all 380 rows.

    Args:
        match_id: FootyStats match id.
        force: Re-request even when archived.

    Returns:
        The raw JSON body.
    """
    return get(ENDPOINT_MATCH_DETAIL, force=force, match_id=match_id)


def fetch_league_table(season_id: int, *, force: bool = False) -> dict[str, Any]:
    """Fetch the published final table for a season.

    A cross-check for phase 04, not a data source. Point-in-time standings still
    have to be reconstructed from results - this endpoint only knows the table
    now, not the table as of some Tuesday in June 2019. But comparing your
    reconstruction's final matchday against this turns phase 04 from "I think
    this is right" into "this matches the published table".

    Args:
        season_id: FootyStats season id.
        force: Re-request even when archived.

    Returns:
        The raw JSON body.
    """
    return get(ENDPOINT_LEAGUE_TABLE, force=force, season_id=season_id)


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
        One row per match as returned, plus season_id, ingested_at,
        source_endpoint, and raw_json (the complete record, compact JSON). No
        type coercion of any API field.

    Raises:
        SchemaDriftError: A required field is absent, or the payload has no
            'data' list. The message names what was found as well as what was
            expected.
    """
    records = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        found = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise SchemaDriftError(
            f"season_id {season_id}: expected a 'data' list of match records; "
            f"found top-level keys {found}"
        )
    if not all(isinstance(record, dict) for record in records):
        kinds = sorted({type(record).__name__ for record in records})
        raise SchemaDriftError(
            f"season_id {season_id}: every entry of 'data' should be a match record "
            f"(object); found {kinds}"
        )

    stamps: dict[str, Any] = {
        "season_id": int(season_id),
        "ingested_at": utcnow(),
        "source_endpoint": ENDPOINT_LEAGUE_MATCHES,
    }
    if not records:
        log.warning("season_id %s: league-matches returned no match records", season_id)
        return pd.DataFrame(columns=[*sorted(REQUIRED_MATCH_FIELDS), *stamps, "raw_json"])

    df = pd.DataFrame(records)
    missing = REQUIRED_MATCH_FIELDS - set(df.columns)
    if missing:
        raise SchemaDriftError(
            f"season_id {season_id}: missing {sorted(missing)}; found {sorted(df.columns)}"
        )
    extra = set(df.columns) - REQUIRED_MATCH_FIELDS
    if extra:
        log.debug("season_id %s: %d unrequired fields (kept)", season_id, len(extra))

    raw_json = [json.dumps(record, separators=(",", ":"), ensure_ascii=False) for record in records]
    df = df.assign(**stamps, raw_json=raw_json)
    log.info(
        "season_id %s: parsed %d match rows with %d fields", season_id, len(df), len(df.columns)
    )
    return df


def _provider_id_text(value: object) -> str:
    """The provider id as text, tolerating pandas' float upcast of int columns."""
    if value is None or (isinstance(value, float) and value != value):
        raise ValueError("add_match_id: every row needs a provider 'id'; found a null")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def add_match_id(df: pd.DataFrame) -> pd.DataFrame:
    """Add a namespaced match_id, from the provider's own id where it exists.

    'fs:' + the FootyStats id. Using a provider id rather than hashing
    season|date|home|away is a real improvement: the hash moved whenever a club
    was renamed, which silently turned updates into inserts. A provider id does
    not move when a club rebrands.

    The namespace prefix costs nothing and buys optionality: if a second source
    is ever needed it gets its own space rather than colliding, and an id in a
    log line says where it came from.

    When the frame carries no 'id' - a second source, or the hand-built test
    fixtures - the fallback is the natural key the scraped version had to use:
    'nk:' + sha1(season|date|home_raw|away_raw)[:16]. The two namespaces
    cannot collide, and the log line says which one was used.

    Args:
        df: Parsed matches carrying the provider id, or the natural-key
            columns season, date, home_raw, away_raw.

    Returns:
        A copy of the frame with match_id added. The input is not mutated.

    Raises:
        ValueError: The frame carries neither an id nor the natural key, or an
            id is null.
    """
    out = df.copy()
    if "id" in out.columns:
        out["match_id"] = ["fs:" + _provider_id_text(value) for value in out["id"]]
        log.info("match_id from the provider id (fs:) for %d row(s)", len(out))
        return out

    if all(column in out.columns for column in NATURAL_KEY_COLUMNS):
        keys = zip(*(out[column] for column in NATURAL_KEY_COLUMNS), strict=True)
        out["match_id"] = [
            "nk:" + hashlib.sha1(f"{season}|{date}|{home}|{away}".encode()).hexdigest()[:16]
            for season, date, home, away in keys
        ]
        log.info("match_id from the natural key (nk:) for %d row(s) - no provider id", len(out))
        return out

    raise ValueError(
        "add_match_id needs either a provider 'id' column or all of "
        f"{list(NATURAL_KEY_COLUMNS)}; found columns {list(out.columns)}"
    )
