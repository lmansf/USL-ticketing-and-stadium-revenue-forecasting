"""Why is the Dagster UI a white screen? Fetch what the page loads and name the part that fails.

A white screen means the browser got a page and the app inside it did not
run. The server can still be perfectly healthy - /server_info answering is
not the UI - so this fetches, outside any browser, exactly what the browser
would: the versions, the page, every script the page names, and a GraphQL
query, and says which of them is wrong. Nothing here needs a key or writes
anything.

    python scripts/check_dagster_ui.py                          # against http://127.0.0.1:3000
    python scripts/check_dagster_ui.py --url http://127.0.0.1:3050

Run it while `make dagster` is up and the terminal has printed
'Serving dagster-webserver'. Exit code 0 means every part came back as it
should and the fault is in the browser itself; 1 names what did not; 2 means
the server did not answer at all.

See docs/phases/11-phase-two-dagster.md, "A white screen in the browser".
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

import requests

DEFAULT_URL = "http://127.0.0.1:3000"
# A healthy dagster-webserver ships several hundred files under webapp/build;
# a build this small is a broken or partial install.
MIN_BUILD_FILES = 100
TIMEOUT = 20


def _get(session: requests.Session, url: str) -> requests.Response:
    return session.get(url, timeout=TIMEOUT)


def check_server_info(session: requests.Session, base: str, problems: list[str]) -> bool:
    """The three versions; False when the server does not answer at all."""
    try:
        response = _get(session, f"{base}/server_info")
        info: dict[str, Any] = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"server_info: {type(exc).__name__}: {exc}")
        return False
    print(f"server_info: {info}")
    versions = {str(v) for k, v in info.items() if k.endswith("_version")}
    if len(versions) > 1:
        problems.append(
            "dagster, dagster-webserver and dagster-graphql are not at one version; "
            "pip install all three at the dagster version"
        )
    return True


def check_installed_build(problems: list[str]) -> None:
    """The webapp the server serves from, on this machine's Python."""
    try:
        import dagster_webserver
    except ImportError:
        problems.append(
            'dagster_webserver is not importable from this Python; pip install -e ".[dagster]" '
            "in the same environment that runs make dagster"
        )
        return
    build = os.path.join(os.path.dirname(dagster_webserver.__file__), "webapp", "build")
    count = sum(len(files) for _, _, files in os.walk(build))
    print(f"webapp build: {build} ({count} files)")
    if count < MIN_BUILD_FILES:
        problems.append(
            f"the installed webapp build has {count} files where a healthy install has several "
            "hundred, so the page's scripts cannot be served; reinstall it: "
            "pip install --force-reinstall --no-cache-dir dagster-webserver==<dagster version>"
        )


def check_page_and_scripts(session: requests.Session, base: str, problems: list[str]) -> None:
    """The page, its content-security-policy, and every script it names."""
    try:
        response = _get(session, f"{base}/")
    except requests.RequestException as exc:
        problems.append(f"GET / failed: {type(exc).__name__}: {exc}")
        return
    html = response.text
    content_type = response.headers.get("content-type", "")
    kind = content_type or "(no content-type)"
    print(f"GET /: HTTP {response.status_code}, {len(html)} bytes, {kind}")
    if "Dagster" not in html:
        problems.append(
            "the page at / is not Dagster's (no 'Dagster' in it); another program answers on "
            "this port, or something rewrote the page. Its first 300 characters:\n    "
            + html[:300].replace("\n", " ")
        )
        return

    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    if not scripts:
        problems.append(
            "the page names no script files, so nothing can run; the HTML was changed between "
            "the server and this machine's HTTP client"
        )
        return
    csp = response.headers.get("content-security-policy", "")
    nonce = re.search(r'nonce="([^"]+)"', html)
    if csp and nonce and nonce.group(1) not in csp:
        problems.append(
            "the script nonce in the page is not the one in the Content-Security-Policy header, "
            "so the browser refuses every script; something between the server and the client "
            "rewrites the page or the headers (an antivirus web shield, a proxy)"
        )
    if not csp:
        print("  (no Content-Security-Policy header; the server sends one, a proxy may strip it)")

    bad = 0
    for src in scripts:
        url = src if src.startswith("http") else f"{base}/{src.lstrip('/')}"
        try:
            got = _get(session, url)
        except requests.RequestException as exc:
            print(f"  BAD {type(exc).__name__:<28} {src}")
            bad += 1
            continue
        kind = got.headers.get("content-type", "")
        ok = got.status_code == 200 and ("javascript" in kind or "ecmascript" in kind)
        flag = "ok " if ok else "BAD"
        print(f"  {flag} HTTP {got.status_code} {len(got.content):>9} bytes {kind[:28]:<28} {src}")
        if not ok:
            bad += 1
    if bad:
        problems.append(
            f"{bad} of {len(scripts)} script files did not come back as JavaScript with HTTP 200. "
            "A 404 means the file is missing from the installed build (a quarantined or "
            "half-installed package: reinstall dagster-webserver); HTML or another type in "
            "its place means something on this machine answers instead of the server"
        )


def check_graphql(session: requests.Session, base: str, problems: list[str]) -> None:
    """The query the UI makes first."""
    try:
        response = session.post(f"{base}/graphql", json={"query": "{ version }"}, timeout=TIMEOUT)
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        problems.append(f"POST /graphql failed: {type(exc).__name__}: {exc}")
        return
    print(f"graphql: HTTP {response.status_code} {payload}")
    if response.status_code != 200 or "data" not in payload:
        problems.append("the GraphQL endpoint did not answer the version query; the UI cannot load")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"the UI's address (default {DEFAULT_URL})"
    )
    args = parser.parse_args(argv)
    base = str(args.url).rstrip("/")

    session = requests.Session()
    session.trust_env = False  # no proxy variables: this is a loopback request
    problems: list[str] = []
    if not check_server_info(session, base, problems):
        print(
            f"\nVerdict: nothing answers at {base}. Start it with make dagster and wait for the "
            "terminal to print 'Serving dagster-webserver' (about thirty seconds); if it printed "
            "a different port, pass --url."
        )
        return 2
    check_installed_build(problems)
    check_page_and_scripts(session, base, problems)
    check_graphql(session, base, problems)

    print()
    if problems:
        print("Verdict: the UI cannot render because")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "Verdict: the server, the page, every script and the GraphQL endpoint are all as they "
        "should be outside a browser. What is left is the browser: press F12, open Console, "
        "reload, and read the first red line."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
