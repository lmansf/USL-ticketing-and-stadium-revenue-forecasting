"""Demonstrate schema drift detection. NOT a staged failure.

Feed a saved HTML fixture with a renamed column. The parser raises, naming
expected versus found. Correct behaviour on display, not a bug being patched.

The fixture is a normal saved page with one column header edited. Keep it in
demo/fixtures/ - it is shared with the test suite.

Doc: docs/phases/09-break-and-fix.md, "Demonstrate working, do not break"
"""

from __future__ import annotations


def main() -> None:
    """Parse the drifted fixture and print the SchemaDriftError message.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/09-break-and-fix.md")


if __name__ == "__main__":
    main()
