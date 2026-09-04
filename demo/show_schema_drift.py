"""Demonstrate schema drift detection. NOT a staged failure.

Feed an archived response with a required field removed. The parser raises,
naming expected versus found. Correct behaviour on display, not a bug being
patched.

This matters more with an API than it did with scraped HTML, and for a specific
reason: the match-detail endpoint is undocumented. It carries no versioning
promise, no deprecation notice, and no guarantee its field set is the same for
USL as for the leagues it was presumably built against. A guard that fails loudly
is the only warning you will get.

The fixture is a real archived payload with one field deleted. It lives in
demo/fixtures/ rather than data/raw_archive/, because the archive is meant to be
a faithful record of what the API actually said.

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
