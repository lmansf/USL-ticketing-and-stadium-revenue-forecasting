"""D2 - A failed API request surfaces as a failed stage.

Point an ingest call at a bad season id, or run with a revoked key. The client
raises an error carrying the endpoint and the HTTP status. Fix, re-run.

What it shows: upstream failure surfaces as a failed asset, not as corrupt data.
The alternative - a client that catches the error, logs a warning, and returns an
empty payload - produces a green run and a mart missing a season. That is the
version that gets shipped by accident.

Four beats, no network:

  archived  an archived request keeps working with no key at all - the half of
            this scenario that matters after the subscription month ends.
  no key    an unarchived season id with no key raises NoSubscriptionError before
            any request is made.
  404       a fake key and a stubbed 404: the endpoint path or season id is wrong.
  401       a fake key and a stubbed 401: the key is wrong or the month has ended.
            The one that will really happen to you.

Neither status is retried - the stub counts its calls to prove it. And the
messages name the endpoint and the status but NOT the request URL, because the
key is in it; the fake key is asserted absent from every message and every log
line.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from usl import config  # noqa: E402
from usl.ingest import archive, footystats  # noqa: E402
from usl.ingest.footystats import FootyStatsError, NoSubscriptionError  # noqa: E402

FAKE_KEY = "demo-not-a-real-key"
BAD_SEASON = 999999
ENDPOINT = footystats.ENDPOINT_LEAGUE_MATCHES


class FakeResponse:
    """The two attributes the client reads. Deliberately no .url."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = '{"success": false, "message": "stubbed by demo/d2_dead_url.py"}'


class StubGet:
    """Stands in for requests.get: always the same status, counts its calls."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls += 1
        return FakeResponse(self.status_code)


class Capture(logging.Handler):
    """Keeps every formatted log line so the demo can search them for the key."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def say(text: str) -> None:
    print(f"\n== {text}")


def check(condition: bool, what: str) -> bool:
    print(("   ok   " if condition else "   FAIL ") + what)
    return condition


def message_is_clean(message: str) -> bool:
    return (
        check(ENDPOINT in message, f"names the endpoint '{ENDPOINT}'")
        & check(FAKE_KEY not in message, "does not carry the key")
        & check(
            "://" not in message and "key=" not in message.lower(),
            "does not carry a URL or a query string",
        )
    )


def main() -> int:
    """Show the archive hit, the no-key error, and the stubbed 404 and 401."""
    say("D2 - the failed API request")
    print("No network is used. The key is a fake string set only for this process;")
    print("requests.get is replaced by a stub that answers 404, then 401.")

    logging.basicConfig(level=logging.INFO, format="   | %(levelname)-7s %(name)s: %(message)s")
    capture = Capture()
    capture.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(capture)

    saved_key = config.FOOTYSTATS_API_KEY
    saved_env = os.environ.get("FOOTYSTATS_API_KEY")
    saved_get = requests.get
    ok = True
    try:
        say("archived request, no key")
        config.FOOTYSTATS_API_KEY = ""
        os.environ.pop("FOOTYSTATS_API_KEY", None)
        payload = footystats.get(ENDPOINT, season_id=config.EXAMPLE_SEASON_ID)
        ok &= check(
            len(payload["data"]) == 380,
            f"season_id {config.EXAMPLE_SEASON_ID} served from the archive: "
            f"{len(payload['data'])} matches, no key needed",
        )

        say(f"unarchived season_id {BAD_SEASON}, no key")
        try:
            footystats.get(ENDPOINT, season_id=BAD_SEASON)
            ok &= check(False, "NoSubscriptionError was raised")
        except NoSubscriptionError as exc:
            print(f"   NoSubscriptionError: {exc}")
            ok &= check(True, "NoSubscriptionError raised before any request")
            ok &= check(str(BAD_SEASON) in str(exc), "names the parameters")
            ok &= message_is_clean(str(exc))

        for status, expect in ((404, "season id is wrong"), (401, "subscription has lapsed")):
            say(f"fake key in the environment, the API answers {status}")
            config.FOOTYSTATS_API_KEY = FAKE_KEY
            os.environ["FOOTYSTATS_API_KEY"] = FAKE_KEY
            stub = StubGet(status)
            requests.get = stub  # type: ignore[assignment]
            try:
                footystats.get(ENDPOINT, season_id=BAD_SEASON)
                ok &= check(False, "FootyStatsError was raised")
            except FootyStatsError as exc:
                print(f"   FootyStatsError: {exc}")
                ok &= check(str(status) in str(exc), f"names HTTP {status}")
                ok &= check(expect in str(exc), f"says what {status} means ('{expect}')")
                ok &= message_is_clean(str(exc))
            finally:
                requests.get = saved_get
            ok &= check(stub.calls == 1, f"one request, not retried ({stub.calls} call)")
            ok &= check(
                not archive.is_archived(ENDPOINT, {"season_id": BAD_SEASON}),
                "nothing was written to the archive",
            )

        say("the log")
        leaked = [line for line in capture.lines if FAKE_KEY in line]
        ok &= check(not leaked, f"{len(capture.lines)} log line(s), none carries the key")
    finally:
        requests.get = saved_get
        config.FOOTYSTATS_API_KEY = saved_key
        if saved_env is None:
            os.environ.pop("FOOTYSTATS_API_KEY", None)
        else:
            os.environ["FOOTYSTATS_API_KEY"] = saved_env
        logging.getLogger().removeHandler(capture)

    say("result")
    print(
        "D2 shown: endpoint and status in the message, no URL, no key, no retry, "
        "and the archive keeps serving"
        if ok
        else "D2 NOT shown - see the FAIL lines above"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
