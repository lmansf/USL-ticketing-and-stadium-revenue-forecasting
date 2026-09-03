"""DuckDB write strategy under a held lock.

DELIBERATELY EMPTY OF ASSERTIONS.

usl/db.py::connect_for_write is the one unguided exercise in this project - no
worked solution in the docs, and no worked test here. Writing this test is part
of it, because what you assert depends on which strategy you chose, and choosing
is the exercise.

Scenarios worth covering, whichever route you took:

  1. A second process holds the file. The write either completes or fails with a
     message that names the likely cause.
  2. The job is killed partway through a write. The database is still readable
     and internally consistent afterwards.
  3. A reader opening the file mid-run sees the complete old state or the
     complete new state, never a partial one.
  4. An exception that is NOT a lock - a genuine error - is not retried. Retrying
     the wrong error class turns a real bug into a slow one.
  5. The run log records which of the above happened, so demo scenario D1 has a
     line to point at.

Doc: docs/phases/02-duckdb-and-the-lock-problem.md, "Guard two - handle the lock"
"""

from __future__ import annotations
