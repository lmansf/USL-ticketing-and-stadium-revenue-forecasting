"""D2 - 404 source URL.

Point a season URL at a dead path. The run shows a failed stage carrying the HTTP
status and the URL. Fix, re-run.

What it shows: upstream failure surfaces as a failed asset, not as corrupt data.
The alternative - a scraper that catches the error, logs a warning, and returns an
empty DataFrame - produces a green run and a mart missing a season. That is the
version that gets shipped by accident.

Watch for: if your retry logic retries a 404, this demo takes five minutes of
backoff before failing. A 404 is not transient.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations


def main() -> None:
    """Point a season at a dead URL, run, restore.

    TODO: implement. Monkeypatch or override season_url for one season, run the
    scrape stage, print the failed run_log row, restore.
    """
    raise NotImplementedError("TODO: see docs/phases/09-break-and-fix.md, D2")


if __name__ == "__main__":
    main()
