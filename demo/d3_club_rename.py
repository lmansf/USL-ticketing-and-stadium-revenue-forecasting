"""D3 - The silent one. A club rename drops joined rows.

Edit a club name in club_aliases.csv so matches stop mapping. Run. The check
catches it and names the unmapped string.

The best demo of the four, because in a real pipeline this fails QUIETLY. The club
simply disappears, the row count drops by forty, and no error fires. Silent data
loss is the failure mode that actually bites BI teams, and it is the one nobody
demos because nobody instruments for it.

Show both signals: the check that names the unmapped string, and the row-count log
line that would have caught it even if the check had not.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations


def main() -> None:
    """Break one alias, run the transform, restore in a finally.

    The restore must be in a finally. A failed demo that leaves club_aliases.csv
    edited is a demo you cannot run twice, in front of an audience.

    TODO: implement. See docs/phases/09-break-and-fix.md, exercise 9.1.
    """
    raise NotImplementedError("TODO: see docs/phases/09-break-and-fix.md, D3")


if __name__ == "__main__":
    main()
