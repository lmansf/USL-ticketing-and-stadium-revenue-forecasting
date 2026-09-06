"""D4 - Null injected into a feature column.

Put a null into a feature column. Show whether the model handles it or the check
flags it, and explain which behaviour you chose and why.

What it shows: the answer to "what does your pipeline do with missing data" is a
decision you made, not an accident. Both answers are defensible - XGBoost handles
nulls natively by learning a default split direction, and failing the run is
stricter and safer. What is not defensible is discovering on camera that you do
not know which one happens.

The decision here, recorded in config.ALLOWED_NULL_FEATURES: the four lag
features may be null (a club's first home match has no previous gate) and go to
XGBoost as they are; a null anywhere else fails the run at the features_not_null
check, before training. This demo nulls rank_before on five rows of the mart and
shows both halves: the check fails naming the column and the count, and XGBoost
would have trained on the frame regardless - which is exactly why the check
stands in front of it.

Runs against a scratch copy of the database, deleted afterwards, so the null
never reaches the real mart.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations

import datetime as dt
import logging
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
from usl.models.train import train_all  # noqa: E402
from usl.transform.checks import features_not_null  # noqa: E402

COLUMN = "rank_before"
ROWS = 5


def say(text: str) -> None:
    print(f"\n== {text}")


def check(condition: bool, what: str) -> bool:
    print(("   ok   " if condition else "   FAIL ") + what)
    return condition


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "usl.run", *args]
    print("$ " + " ".join(command[1:]))
    return subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def scratch_database(directory: Path) -> Path:
    """A private copy of the database with a green mart, so the real one is untouched."""
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
    result = run_cli("transform", "--db", str(scratch))
    if result.returncode != 0:
        print(result.stderr)
        raise SystemExit("the transform must be green before the null goes in")
    print("transform green on the copy: every check passed, the mart is clean")
    return scratch


def main() -> int:
    """Inject the null, run the check, train anyway, state the decision, delete the copy."""
    say("D4 - the null injection")
    print(f"Set {COLUMN} to NULL on {ROWS} rows of mart_match_features (a scratch copy),")
    print("run the mart check, then train on the frame anyway to show what XGBoost does.")

    logging.basicConfig(level=logging.INFO, format="   | %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("usl.models.train").setLevel(logging.WARNING)

    ok = True
    with tempfile.TemporaryDirectory(prefix="usl-d4-") as tmp:
        scratch = scratch_database(Path(tmp))
        con = duckdb.connect(str(scratch))
        try:
            say("inject")
            con.execute(
                f"""
                UPDATE mart_match_features SET {COLUMN} = NULL
                WHERE match_id IN (
                    SELECT match_id FROM mart_match_features ORDER BY date, match_id LIMIT {ROWS}
                )
                """
            )
            row = con.execute(
                f"SELECT count(*) FROM mart_match_features WHERE {COLUMN} IS NULL"
            ).fetchone()
            nulls = int(row[0]) if row else 0
            print(f"mart_match_features now has {nulls} null(s) in {COLUMN}")
            ok &= check(nulls == ROWS, f"{ROWS} rows nulled")

            say("the check")
            result = features_not_null(con)
            print(f"   features_not_null passed={result.passed}")
            print(f"   null_counts        = {result.metadata['null_counts']}")
            print(f"   allowed_with_nulls = {result.metadata['allowed_with_nulls']}")
            ok &= check(not result.passed, "the check fails")
            ok &= check(
                result.metadata["null_counts"] == {COLUMN: ROWS},
                f"it names {COLUMN} with a count of {ROWS}",
            )
            print(
                "   In the pipeline this check runs right after the mart is built. A failure "
                "raises CheckFailure, the transform stage is recorded as failed, the exit code "
                "is 1, and train never starts."
            )

            say("what XGBoost would do with the same frame")
            summary = train_all(con, dt.date.today(), seeds=(config.RANDOM_STATE,))
            for name, mae in summary["mae"].items():
                print(f"   {name:16s} MAE {mae:8.1f}")
            ok &= check(
                "prorel" in summary["mae"],
                f"XGBoost trained on the frame with {ROWS} null {COLUMN} values - it learns a "
                "default split direction for a missing value and never complains",
            )

            say("the decision")
            print(
                "   Both behaviours are real. XGBoost tolerates the null, so nothing downstream "
                "would have noticed: a mart column that quietly went null one Tuesday would "
                "train, score, and ship. That is why the pipeline does not rely on it. "
                f"{COLUMN} is not in config.ALLOWED_NULL_FEATURES, so features_not_null fails "
                "the run and a null here never reaches training in the real pipeline. The only "
                "nulls that do are the four lag features, where the null has a meaning (no "
                "previous home gate) and no imputation would be more honest than XGBoost's own "
                "handling."
            )
        finally:
            con.close()
    print(f"\nscratch copy deleted; {config.DB_PATH.name} was never opened")

    say("result")
    print(
        "D4 shown: the check fails naming the column, the model would not have, and that is "
        "the decision"
        if ok
        else "D4 NOT shown - see the FAIL lines above"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
