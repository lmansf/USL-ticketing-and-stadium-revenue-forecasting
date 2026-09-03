"""Demonstrate idempotency. NOT a staged failure.

Run Tuesday's job twice. The second run reports zero inserted, N updated, and
attendance totals unchanged. This works out of the box by design.

Frame it as: "re-running is safe, and here is the log line that proves it."

Doc: docs/phases/09-break-and-fix.md, "Demonstrate working, do not break"
"""

from __future__ import annotations


def main() -> None:
    """Run the load twice, print both LoadStats splits and the attendance totals.

    TODO: implement.
    """
    raise NotImplementedError("TODO: see docs/phases/09-break-and-fix.md")


if __name__ == "__main__":
    main()
