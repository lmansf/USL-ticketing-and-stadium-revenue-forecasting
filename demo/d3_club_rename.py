"""D3 - The silent one. A club rename drops joined rows.

Edit a club name in club_aliases.csv so matches stop mapping. Run. The check
catches it and names the unmapped string.

The best demo of the four, because in a real pipeline this fails QUIETLY. The club
simply disappears, the row count drops by thirty-eight, and no error fires.
Silent data loss is the failure mode that actually bites BI teams, and it is the
one nobody demos because nobody instruments for it.

Two signals are shown. First, all_clubs_mapped fails naming the exact string
('93', Manchester City's provider id) so the fix is a paste, not an
investigation. Second, the row count: the staging join is a LEFT JOIN, so the
380 rows survive with null club ids - and the demo counts what an INNER JOIN
would have left (342), which is the drop row_count_preserved exists to catch.

The edit is made to the real usl/ref/club_aliases.csv, because that is the
point, and restored byte for byte in a finally. The transform runs against a
scratch copy of the database.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from usl import config  # noqa: E402

ALIASES = config.CLUB_ALIASES_CSV
PROVIDER_ID = "93"  # Manchester City's FootyStats club id, one row of club_aliases.csv
RENAMED_TO = "9393"


def say(text: str) -> None:
    print(f"\n== {text}")


def check(condition: bool, what: str) -> bool:
    print(("   ok   " if condition else "   FAIL ") + what)
    return condition


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "usl.run", *args]
    print("$ " + " ".join(command[1:]))
    return subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def show(lines: list[str]) -> None:
    for line in lines:
        print("   | " + line)


def scratch_database(directory: Path) -> Path:
    """A private copy of the database, so the real one never sees the broken run."""
    scratch = directory / "usl_demo.duckdb"
    if config.DB_PATH.exists():
        shutil.copyfile(config.DB_PATH, scratch)
        print(f"copied {config.DB_PATH} to {scratch}")
    else:
        print(f"{config.DB_PATH} does not exist yet - building the scratch copy from the archive")
        result = run_cli("backfill", "--db", str(scratch))
        if result.returncode != 0:
            print(result.stderr)
            raise SystemExit("could not build the scratch database")
    return scratch


def broken_aliases(text: str) -> str:
    """The CSV with the one provider-id row renamed, everything else untouched."""
    lines = text.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.startswith(PROVIDER_ID + ",")]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one row starting '{PROVIDER_ID},' in {ALIASES}")
    lines[hits[0]] = RENAMED_TO + lines[hits[0]][len(PROVIDER_ID) :]
    return "".join(lines)


def inner_join_count(path: Path) -> tuple[int, int]:
    """Rows staging kept, and rows an INNER JOIN would have kept."""
    con = duckdb.connect(str(path), read_only=True)
    try:
        row = con.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE home_club_id IS NOT NULL AND away_club_id IS NOT NULL)
            FROM stg_matches
            """
        ).fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)
    finally:
        con.close()


def failed_run_checks(path: Path) -> list[tuple[str, bool, str]]:
    """The check_log rows of the most recent failed run in the scratch copy."""
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT check_name, passed, metadata
            FROM check_log
            WHERE run_id = (
                SELECT run_id FROM run_log WHERE status = 'failed'
                ORDER BY started_at DESC LIMIT 1
            )
            ORDER BY checked_at
            """
        ).fetchall()
        return [(str(r[0]), bool(r[1]), str(r[2])) for r in rows]
    finally:
        con.close()


def main() -> int:
    """Break one alias, run the transform, show both signals, restore in a finally."""
    say("D3 - the club rename")
    print(f"Rename the raw_name '{PROVIDER_ID}' (Manchester City) to '{RENAMED_TO}' in")
    print(f"{ALIASES.relative_to(REPO_ROOT)}, run the transform, and watch the check name it.")

    original = ALIASES.read_bytes()
    ok = True
    with tempfile.TemporaryDirectory(prefix="usl-d3-") as tmp:
        try:
            scratch = scratch_database(Path(tmp))

            say("break the alias")
            ALIASES.write_text(broken_aliases(original.decode("utf-8")), encoding="utf-8")
            print(f"row '{PROVIDER_ID},manchester_city,...' now reads '{RENAMED_TO},...'")

            say("run the transform")
            failed = run_cli("transform", "--db", str(scratch))
            print(f"exit code {failed.returncode}")
            show(
                [
                    line
                    for line in failed.stderr.splitlines()
                    if "materialised stg_matches" in line
                    or "check all_clubs_mapped" in line
                    or "check row_count_preserved" in line
                    or "stage transform FAILED" in line
                ]
            )
            ok &= check(failed.returncode == 1, "exit code 1: a check failed the run")
            ok &= check(
                f'"unmapped": ["{PROVIDER_ID}"]' in failed.stderr,
                f"all_clubs_mapped names the unmapped string '{PROVIDER_ID}'",
            )
            ok &= check(
                "materialised int_standings" not in failed.stderr,
                "the intermediate tier was never built on the broken staging",
            )

            say("signal one: the check, as recorded in check_log")
            recorded = failed_run_checks(scratch)
            for name, passed, metadata in recorded:
                print(f"   {name:34s} {'passed' if passed else 'FAILED'}  {metadata}")
            ok &= check(
                any(name == "all_clubs_mapped" and not passed for name, passed, _ in recorded),
                "check_log carries the failure with its metadata",
            )

            say("signal two: the row count")
            kept, inner = inner_join_count(scratch)
            print(f"   staging rows kept by the LEFT JOIN:          {kept}")
            print(f"   rows an INNER JOIN would have kept:          {inner}")
            print(f"   rows that would have vanished, no error:     {kept - inner}")
            print(
                "   row_count_preserved compares raw to staging; with an inner join it would "
                f"read raw_rows={kept} staging_rows={inner} difference={inner - kept} and fail "
                "even if all_clubs_mapped did not exist."
            )
            ok &= check(kept - inner == 38, "one club's 38 matches are the rows at stake")
        finally:
            ALIASES.write_bytes(original)
            restored = ALIASES.read_bytes() == original
            print(f"\nrestored {ALIASES.relative_to(REPO_ROOT)} byte for byte: {restored}")
            ok &= restored

        say("fix (the file is back) and re-run")
        green = run_cli("transform", "--db", str(scratch))
        print(f"exit code {green.returncode}")
        show(
            [
                line
                for line in green.stderr.splitlines()
                if "check all_clubs_mapped" in line or "stage transform finished" in line
            ]
        )
        ok &= check(green.returncode == 0, "exit code 0 after the fix")

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", str(ALIASES)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode == 0:
            ok &= check(status.stdout.strip() == "", "git status is clean for club_aliases.csv")
    except OSError:
        pass

    say("result")
    print(
        "D3 shown: the check names the string, the row count shows the silent loss it prevents"
        if ok
        else "D3 NOT shown - see the FAIL lines above"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
