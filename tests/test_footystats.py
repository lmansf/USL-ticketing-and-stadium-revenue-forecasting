"""FootyStats client: archiving, key handling, and schema drift.

Runs against a committed example-key fixture, so it needs no subscription and
keeps working forever. The network is never touched: requests.get is replaced
by a scripted stand-in that records what it was asked, and the archive lives in
tmp_path.

Doc: docs/phases/00-data-access-and-the-clock.md
     docs/phases/01-ingest-to-raw.md
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

from usl import config
from usl.ingest import archive
from usl.ingest import footystats as fs
from usl.ingest.footystats import (
    FootyStatsError,
    NoSubscriptionError,
    SchemaDriftError,
    add_match_id,
    parse_season_matches,
)
from usl.logging_setup import RedactSecretsFilter

# A stand-in credential. Distinctive enough that a substring search for it in a
# filename, a message, or a log record cannot match by accident.
FAKE_KEY = "sekrit-key-9f3a7c"

THROTTLE = 0.5

MATCH: dict[str, Any] = {
    "id": 1,
    "season": "2018/2019",
    "status": "complete",
    "date_unix": 1533927600,
    "homeID": 149,
    "awayID": 108,
    "homeGoalCount": 2,
    "awayGoalCount": 1,
    "attendance": 74439,
}

PAYLOAD: dict[str, Any] = {
    "success": True,
    "pager": {"current_page": 1, "max_page": 1, "results_per_page": 600, "total_results": 1},
    "data": [MATCH],
}


class FakeResponse:
    """The two attributes the client reads. Deliberately no .url: the client must not need it."""

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeGet:
    """A requests.get stand-in that replays scripted outcomes and records every call.

    An outcome is a FakeResponse to return or an exception to raise. Running
    out of outcomes is a test failure: the client asked for more requests than
    the test allowed.
    """

    def __init__(self, *outcomes: FakeResponse | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, url: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> FakeResponse:
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        if not self.outcomes:
            raise AssertionError("requests.get was called more times than the test scripted")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def ok(payload: object) -> FakeResponse:
    """A 200 carrying the JSON encoding of payload."""
    return FakeResponse(200, json.dumps(payload))


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Archive in tmp_path, no key, no waiting, and no network.

    requests.get starts as a FakeGet with nothing scripted, so a test that does
    not expect a request fails loudly if one is made. Returns the list every
    sleep the client asks for is appended to.
    """
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "raw_archive")
    monkeypatch.setattr(config, "FOOTYSTATS_API_KEY", "")
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", THROTTLE)
    monkeypatch.setattr(config, "FETCH_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(config, "FETCH_BACKOFF_BASE_SECONDS", 2.0)
    sleeps: list[float] = []
    monkeypatch.setattr(fs, "_sleep", sleeps.append)
    monkeypatch.setattr(requests, "get", FakeGet())
    return sleeps


def use_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FOOTYSTATS_API_KEY", FAKE_KEY)


def use_get(monkeypatch: pytest.MonkeyPatch, *outcomes: FakeResponse | Exception) -> FakeGet:
    fake = FakeGet(*outcomes)
    monkeypatch.setattr(requests, "get", fake)
    return fake


# --------------------------------------------------------------------------
# Archive first, parse second
# --------------------------------------------------------------------------


def test_response_is_archived_before_parsing(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed payload must still land on disk.

    The whole point of archive-before-parse: a JSONDecodeError costs you a
    debugging session, not a request you cannot get back.
    """
    use_key(monkeypatch)
    body = "<html>502 Bad Gateway, but with a 200 status</html>"
    fake = use_get(monkeypatch, FakeResponse(200, body))

    with pytest.raises(json.JSONDecodeError):
        fs.get("league-matches", season_id=1625)

    path = archive.archive_path("league-matches", {"season_id": 1625})
    assert path.read_text(encoding="utf-8") == body, "the body must be on disk, unaltered"
    assert len(fake.calls) == 1


def test_archived_request_is_not_refetched(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """During the subscription, never spend a request twice."""
    use_key(monkeypatch)
    archive.write_archive("league-matches", {"season_id": 1625}, json.dumps(PAYLOAD))
    fake = use_get(monkeypatch, ok({"data": []}))
    caplog.set_level(logging.INFO)

    assert fs.get("league-matches", season_id=1625) == PAYLOAD
    assert fake.calls == [], "an archived request must not reach the network"
    assert sandbox == [], "and must not throttle either - nothing was requested"
    assert "archive hit league-matches_season_id_1625.json" in caplog.text


def test_runs_from_archive_with_no_api_key(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance test for the whole data-access phase.

    With no key set, an archived request must still be served. If this passes,
    the subscription can lapse and the project survives - and anyone cloning the
    repo can run it without paying anything.
    """
    assert config.FOOTYSTATS_API_KEY == "" and not config.has_subscription()
    archive.write_archive("league-matches", {"season_id": 1625}, json.dumps(PAYLOAD))
    fake = use_get(monkeypatch)

    payload = fs.fetch_season_matches(1625)

    assert payload["data"] == [MATCH]
    assert fake.calls == []


def test_unarchived_request_without_key_raises_named_error(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one genuinely bad case, made legible.

    A code path asking for something nobody pulled, discovered after access is
    gone. NoSubscriptionError says that; a bare 401 does not.
    """
    fake = use_get(monkeypatch)

    with pytest.raises(NoSubscriptionError) as exc:
        fs.get("league-matches", season_id=4242)

    message = str(exc.value)
    assert "league-matches" in message and "4242" in message, "names the request"
    assert "data/raw_archive/" in message and "FOOTYSTATS_API_KEY" in message
    assert "://" not in message, "never a URL"
    assert fake.calls == [], "raised before any network call"
    assert sandbox == [], "and before the throttle"
    assert archive.archive_summary()["files"] == 0


def test_force_refetches_and_overwrites_the_archive(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force spends a request on purpose, to correct an archived response."""
    use_key(monkeypatch)
    archive.write_archive("league-matches", {"season_id": 1625}, json.dumps({"data": []}))
    fake = use_get(monkeypatch, ok(PAYLOAD))

    assert fs.get("league-matches", force=True, season_id=1625) == PAYLOAD
    assert len(fake.calls) == 1
    assert archive.read_archived("league-matches", {"season_id": 1625}) == PAYLOAD


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------


def test_api_key_never_appears_in_archive_filenames(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive goes into git. The key must not travel with it."""
    use_key(monkeypatch)
    fake = use_get(monkeypatch, ok(PAYLOAD))

    fs.get("league-matches", season_id=1625)

    files = sorted(config.ARCHIVE_DIR.iterdir())
    assert [p.name for p in files] == ["league-matches_season_id_1625.json"]
    assert FAKE_KEY not in files[0].read_text(encoding="utf-8")
    # The request itself did carry the key - that is the one place it belongs.
    assert fake.calls[0]["params"] == {"season_id": 1625, "key": FAKE_KEY}
    # And archive_path drops a key however it is spelled, as the last line of defence.
    for spelling in ("key", "KEY", "Key"):
        path = archive.archive_path("league-matches", {spelling: FAKE_KEY, "season_id": 1625})
        assert path.name == "league-matches_season_id_1625.json"


def test_api_key_never_appears_in_logs(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The key is in every request URL, so a logger recording full URLs leaks it.

    logs/ is gitignored, but one careless 'git add -f' is all it takes, and a
    paid credential in a public repo is someone else's month on your card.

    Two guards, both asserted here: the client never logs a URL or a requests
    exception (whose text carries the URL), and RedactSecretsFilter scrubs the
    key out of anything else.
    """
    use_key(monkeypatch)
    caplog.set_level(logging.DEBUG)
    leaky_text = f"HTTPSConnectionPool: {fs.BASE_URL}/league-matches?season_id=3&key={FAKE_KEY}"
    fake = use_get(
        monkeypatch,
        FakeResponse(503, "busy"),
        ok(PAYLOAD),
        FakeResponse(404, "no such season"),
        requests.ConnectionError(leaky_text),
    )

    # A retried success, a 4xx failure, and a connection error that gives up.
    fs.get("league-matches", season_id=1)
    with pytest.raises(FootyStatsError):
        fs.get("league-matches", season_id=2)
    monkeypatch.setattr(config, "FETCH_MAX_ATTEMPTS", 1)
    with pytest.raises(FootyStatsError) as exc:
        fs.get("league-matches", season_id=3)

    assert len(fake.calls) == 4
    assert FAKE_KEY not in str(exc.value)
    assert exc.value.__cause__ is None, "chaining the requests exception would print its URL"
    assert exc.value.__suppress_context__ is True

    assert len(caplog.records) >= 4, "the client must log for this assertion to mean anything"
    for record in caplog.records:
        text = record.getMessage()
        assert FAKE_KEY not in text, f"key leaked into a log record: {text!r}"
        assert FAKE_KEY not in repr(record.args)
        assert "://" not in text, f"a URL was logged: {text!r}"

    # The second guard: the filter scrubs both a key= query parameter and the
    # configured secret wherever they appear.
    scrub = RedactSecretsFilter(secret=FAKE_KEY)
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "GET ?season_id=1&key=abc then %s", (FAKE_KEY,), None
    )
    assert scrub.filter(record) is True, "the filter redacts, it does not drop records"
    scrubbed = record.getMessage()
    assert "key=abc" not in scrubbed and FAKE_KEY not in scrubbed
    assert scrubbed == "GET ?season_id=1&key=*** then ***"


# --------------------------------------------------------------------------
# Retries
# --------------------------------------------------------------------------


def test_4xx_is_not_retried(sandbox: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 means the endpoint or season id is wrong. Waiting does not fix that.

    Demo scenario D2: retrying it would burn minutes of backoff to deliver the
    same answer, and inside a 30-day window that time is not free.
    """
    use_key(monkeypatch)
    fake = use_get(monkeypatch, FakeResponse(404, "not found"), ok(PAYLOAD))

    with pytest.raises(FootyStatsError) as exc:
        fs.get("league-matches", season_id=1625)

    message = str(exc.value)
    assert "league-matches" in message and "404" in message, "names endpoint and status"
    assert "season id" in message
    assert FAKE_KEY not in message and "://" not in message
    assert len(fake.calls) == 1, "one attempt, no retry"
    assert sandbox == [THROTTLE], "the throttle only - no backoff sleep"
    assert not archive.is_archived("league-matches", {"season_id": 1625})


def test_401_names_the_subscription(sandbox: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure that will really happen, on the day the month ends."""
    use_key(monkeypatch)
    fake = use_get(monkeypatch, FakeResponse(401, "unauthorised"), ok(PAYLOAD))

    with pytest.raises(FootyStatsError) as exc:
        fs.get("league-matches", season_id=1625)

    assert "401" in str(exc.value) and "subscription" in str(exc.value)
    assert len(fake.calls) == 1


def test_5xx_is_retried_then_succeeds(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A 5xx is the server's problem, and usually a brief one."""
    use_key(monkeypatch)
    caplog.set_level(logging.WARNING)
    fake = use_get(monkeypatch, FakeResponse(503, "busy"), ok(PAYLOAD))

    assert fs.get("league-matches", season_id=1625) == PAYLOAD

    assert len(fake.calls) == 2
    assert sandbox == [THROTTLE, 2.0], "throttle, then one backoff of the base delay"
    assert archive.is_archived("league-matches", {"season_id": 1625})
    assert any("503" in r.getMessage() and "retrying" in r.getMessage() for r in caplog.records)


def test_transient_failures_give_up_after_max_attempts(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connection errors, timeouts and 5xx all count; the backoff doubles; then it stops."""
    use_key(monkeypatch)
    fake = use_get(
        monkeypatch,
        requests.ConnectionError("reset"),
        requests.Timeout("slow"),
        FakeResponse(500, "broken"),
        ok(PAYLOAD),
    )

    with pytest.raises(FootyStatsError) as exc:
        fs.get("league-matches", season_id=1625)

    assert len(fake.calls) == 3
    assert sandbox == [THROTTLE, 2.0, 4.0]
    assert "3 attempt" in str(exc.value) and "HTTP 500" in str(exc.value)
    assert not archive.is_archived("league-matches", {"season_id": 1625})


def test_error_payload_is_not_left_in_the_archive(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 whose body says success=false must not become a permanent archive hit."""
    use_key(monkeypatch)
    fake = use_get(monkeypatch, ok({"success": False, "message": "Invalid season id"}), ok(PAYLOAD))

    with pytest.raises(FootyStatsError) as exc:
        fs.get("league-matches", season_id=1625)

    assert "league-matches" in str(exc.value) and "Invalid season id" in str(exc.value)
    assert not archive.is_archived("league-matches", {"season_id": 1625})
    # The next call requests again rather than serving the error body as a hit.
    assert fs.get("league-matches", season_id=1625) == PAYLOAD
    assert len(fake.calls) == 2


# --------------------------------------------------------------------------
# The archive module on its own
# --------------------------------------------------------------------------


def test_archive_path_is_sorted_readable_and_safe(sandbox: list[float]) -> None:
    """Same request, same filename, whatever the dict order; nothing unsafe in it."""
    a = archive.archive_path("league-matches", {"season_id": 1625, "page": 2})
    b = archive.archive_path("league-matches", {"page": 2, "season_id": 1625})
    assert a == b
    assert a.name == "league-matches_page_2_season_id_1625.json"
    assert a.parent == config.ARCHIVE_DIR
    assert archive.archive_path("league-list", {}).name == "league-list.json"
    odd = archive.archive_path("match", {"season": "2018/2019 x"})
    assert odd.name == "match_season_2018_2019_x.json"


def test_read_archived_names_the_missing_path(sandbox: list[float]) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        archive.read_archived("league-tables", {"season_id": 1})
    assert "league-tables_season_id_1.json" in str(exc.value)


def test_write_archive_is_byte_faithful(sandbox: list[float]) -> None:
    """No pretty-printing, no newline translation, no re-encoding."""
    body = '{"data": [],\r\n "club": "Atlético"}'
    path = archive.write_archive("match", {"match_id": 7}, body)
    assert path.read_bytes() == body.encode("utf-8")
    assert archive.is_archived("match", {"match_id": 7})


def test_archive_summary_counts_files_and_season_ids(sandbox: list[float]) -> None:
    """The 'what have I not pulled yet' report, on an empty and a populated archive."""
    assert archive.archive_summary() == {
        "files": 0,
        "bytes": 0,
        "endpoints": {},
        "season_ids": [],
        "oldest": None,
        "newest": None,
        "directory": str(config.ARCHIVE_DIR),
    }
    archive.write_archive("league-matches", {"season_id": 1625}, "{}")
    archive.write_archive("league-matches", {"season_id": 1626, "page": 2}, "{}")
    archive.write_archive("league-tables", {"season_id": 1625}, "{}")
    archive.write_archive("league-list", {}, "{}")

    summary = archive.archive_summary()
    assert summary["files"] == 4 and summary["bytes"] == 8
    assert summary["endpoints"] == {"league-list": 1, "league-matches": 2, "league-tables": 1}
    assert summary["season_ids"] == [1625, 1626]
    assert summary["oldest"] is not None and summary["newest"] >= summary["oldest"]


# --------------------------------------------------------------------------
# Paging and the league list
# --------------------------------------------------------------------------


def test_fetch_season_matches_concatenates_pages(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every page is archived on its own; the returned payload spans them all."""
    use_key(monkeypatch)
    pager = {"current_page": 1, "max_page": 2, "results_per_page": 1, "total_results": 2}
    page1 = {"success": True, "pager": pager, "data": [MATCH]}
    page2 = {"success": True, "pager": {**pager, "current_page": 2}, "data": [{**MATCH, "id": 2}]}
    fake = use_get(monkeypatch, ok(page1), ok(page2))

    payload = fs.fetch_season_matches(1625)

    assert [m["id"] for m in payload["data"]] == [1, 2]
    assert payload["pager"]["total_results"] == 2
    assert fake.calls[1]["params"] == {"season_id": 1625, "page": 2, "key": FAKE_KEY}
    assert sorted(p.name for p in config.ARCHIVE_DIR.iterdir()) == [
        "league-matches_page_2_season_id_1625.json",
        "league-matches_season_id_1625.json",
    ]


def test_list_leagues_flattens_to_one_row_per_season(
    sandbox: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One row per league-season, and a league with no seasons is still visible."""
    use_key(monkeypatch)
    use_get(
        monkeypatch,
        ok(
            {
                "success": True,
                "data": [
                    {
                        "name": "USA USL Championship",
                        "league_name": "USL Championship",
                        "country": "USA",
                        "season": [{"id": 10, "year": 2019}, {"id": 11, "year": 2020}],
                    },
                    {"name": "Nowhere League", "league_name": "Nowhere", "country": "XX"},
                ],
            }
        ),
    )

    df = fs.list_leagues()

    assert list(df.columns) == ["name", "league_name", "country", "season", "season_id"]
    assert len(df) == 3
    usl = df[df["league_name"] == "USL Championship"]
    assert list(usl["season_id"]) == [10, 11]
    assert list(usl["season"]) == [2019, 2020]
    assert df[df["league_name"] == "Nowhere"]["season_id"].isna().all()


# --------------------------------------------------------------------------
# Schema drift and the parser
# --------------------------------------------------------------------------


def test_missing_required_field_raises_naming_both_sides() -> None:
    """JSON removes the positional-column trap but not schema drift.

    The match-detail endpoint is undocumented, so it carries no versioning
    promise and will not announce a field-set change.
    """
    with pytest.raises(SchemaDriftError) as exc:
        parse_season_matches({"data": [{"id": 1}]}, season_id=1625)
    assert "homeID" in str(exc.value), "must name what is missing"
    assert "found ['id']" in str(exc.value), "and what was there instead"
    assert "1625" in str(exc.value)


def test_payload_without_a_data_list_is_schema_drift() -> None:
    """An error envelope, or a reshaped response, names its top-level keys."""
    with pytest.raises(SchemaDriftError) as exc:
        parse_season_matches({"success": False, "message": "nope"}, season_id=1625)
    assert "message" in str(exc.value) and "success" in str(exc.value)


def test_extra_fields_are_kept_not_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """A field discarded inside the 30-day window cannot be recovered outside it."""
    caplog.set_level(logging.DEBUG)
    record = {**MATCH, "stadium_name": "Old Trafford", "homeGoals": ["12", "45+2"], "odds": 1.5}

    df = parse_season_matches({"data": [record]}, season_id=1625)

    assert {"stadium_name", "homeGoals", "odds"} <= set(df.columns)
    assert df.loc[0, "homeGoals"] == ["12", "45+2"], "list fields survive untouched"
    assert json.loads(df.loc[0, "raw_json"]) == record, "the whole record travels along"
    assert df.loc[0, "season_id"] == 1625
    assert df.loc[0, "source_endpoint"] == "league-matches"
    assert not df["ingested_at"].isna().any()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "extra fields are normal for this API and must not make the log noisy"
    )


def test_empty_season_warns_and_returns_a_stamped_empty_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    df = parse_season_matches({"data": []}, season_id=1625)
    assert df.empty
    assert {"season_id", "ingested_at", "source_endpoint", "raw_json"} <= set(df.columns)
    assert any(r.levelno == logging.WARNING and "1625" in r.getMessage() for r in caplog.records)


def test_match_id_is_namespaced() -> None:
    """'fs:' prefix, so a second source cannot collide and a log line says where
    an id came from."""
    df = pd.DataFrame({"id": [453873, 453874], "season": ["2018/2019", "2018/2019"]})
    out = add_match_id(df)
    assert list(out["match_id"]) == ["fs:453873", "fs:453874"]
    assert "match_id" not in df.columns, "the input frame is not mutated"


@pytest.mark.fixture_required
def test_parses_committed_example_fixture(example_archive_path: Path) -> None:
    """A real archived example-key response parses to the expected row count.

    The EPL 2018/19 season has 380 matches, which is a number you can check
    against the published season rather than against your own parser.
    """
    assert example_archive_path.is_file(), f"missing committed fixture {example_archive_path}"
    payload = json.loads(example_archive_path.read_text(encoding="utf-8"))

    df = add_match_id(parse_season_matches(payload, season_id=1625))

    assert len(df) == 380
    assert df["homeID"].nunique() == 20, "twenty clubs, each with home matches"
    assert set(df["status"]) == {"complete"}
    assert df["match_id"].is_unique
    assert df["match_id"].str.startswith("fs:").all()
    assert (df["season_id"] == 1625).all()
    assert json.loads(df.loc[0, "raw_json"]) == payload["data"][0]
