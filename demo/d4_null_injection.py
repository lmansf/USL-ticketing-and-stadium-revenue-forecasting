"""D4 - Null injected into a feature column.

Put a null into a feature column. Show whether the model handles it or the check
flags it, and explain which behaviour you chose and why.

What it shows: the answer to "what does your pipeline do with missing data" is a
decision you made, not an accident. Both answers are defensible - XGBoost handles
nulls natively by learning a default split direction, and failing the run is
stricter and safer. What is not defensible is discovering on camera that you do
not know which one happens.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations


def main() -> None:
    """Snapshot the database, inject a null, run, restore.

    Snapshot and restore matter here more than in the other scenarios - without
    them the null stays in your mart for the rest of the session.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/09-break-and-fix.md, D4")


if __name__ == "__main__":
    main()
