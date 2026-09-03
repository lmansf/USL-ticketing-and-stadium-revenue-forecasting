"""D1 - The locked DuckDB file.

Open the database in a second process, trigger the run, watch it fail on the
lock with a message that names the cause. Close the holder, re-run, green.

What it shows: the failure is legible, not mysterious. The important half is not
that it failed - it is that six months from now the person reading that log line
knows immediately what to do.

Note: what this demo actually shows depends on which strategy you chose in
usl/db.py::connect_for_write, the one unguided exercise. A retry demo and a
temp-file-swap demo look different and say different things. Whichever you built,
be able to say what happens in the other case.

Doc: docs/phases/09-break-and-fix.md
"""

from __future__ import annotations


def main() -> None:
    """Hold the database open, run the pipeline, restore.

    TODO: implement. Open a writer connection in a subprocess or thread, hold it,
    invoke the weekly run, print the resulting log line, then release.
    """
    raise NotImplementedError("TODO: see docs/phases/09-break-and-fix.md, D1")


if __name__ == "__main__":
    main()
