"""Demonstrate duplicate rejection. NOT a staged failure.

Ingest the same fixture twice. The primary key holds, and the log line shows the
split. Closely related to idempotency, and worth showing as a separate beat
because it is the mechanism underneath it.

Doc: docs/phases/09-break-and-fix.md, "Demonstrate working, do not break"
"""

from __future__ import annotations


def main() -> None:
    """Load one fixture twice, print the row count and the split.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/09-break-and-fix.md")


if __name__ == "__main__":
    main()
