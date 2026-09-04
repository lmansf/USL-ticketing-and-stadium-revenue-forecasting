"""D2 - A failed API request surfaces as a failed stage.

Point an ingest call at a bad season id, or run with a revoked key. The run shows
a failed stage carrying the endpoint and the HTTP status. Fix, re-run.

What it shows: upstream failure surfaces as a failed asset, not as corrupt data.
The alternative - a client that catches the error, logs a warning, and returns an
empty payload - produces a green run and a mart missing a season. That is the
version that gets shipped by accident.

Two variants worth having ready, because they fail differently:

  401  the key is wrong or the subscription lapsed. Not transient. This is the
       one that will really happen to you, on the day the month ends.
  404  the endpoint path or season id is wrong.

Neither is retried. Retrying them burns backoff to deliver the same answer, and
inside a 30-day window that is not free.

Note the error message must name the endpoint and status but NOT the request URL,
because the API key is in it.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations


def main() -> None:
    """Request a bad season id, show the failed stage, restore.

    TODO: implement. Prefer a bad season id over a bad key - it fails the same
    way and does not risk you pasting a real credential into a demo script.
    """
    raise NotImplementedError("TODO: see docs/phases/09-break-and-fix.md, D2")


if __name__ == "__main__":
    main()
