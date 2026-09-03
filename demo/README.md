# Demo scenarios

Fixtures and a short script per scenario. Fumbling for a file mid-demo undoes the
effect.

`make demo-list` prints the menu.

Full walkthrough and the reasoning behind each: [docs/phases/09-break-and-fix.md](../docs/phases/09-break-and-fix.md)

## Break and fix

| | Scenario | Script | Shows |
|---|---|---|---|
| D1 | Locked DuckDB file produces a stale run | `d1_locked_file.py` | The failure is legible, not mysterious |
| D2 | 404 season URL | `d2_dead_url.py` | Upstream failure surfaces as a failed asset, not corrupt data |
| D3 | Club rename silently drops joined rows | `d3_club_rename.py` | Row-count logging catches silent data loss |
| D4 | Null injected into a feature column | `d4_null_injection.py` | The null policy is a decision you made |

D3 is the strongest of the four. In a real pipeline it fails *quietly* - the club
simply disappears - and silent data loss is the failure mode that actually bites
BI teams.

## Demonstrate working, do not break

These are not staged failures. They are correct from day one and shown as such.

| Behaviour | Script | Frame it as |
|---|---|---|
| Idempotency | `show_idempotency.py` | "Re-running is safe, and here is the log line that proves it" |
| Schema drift | `show_schema_drift.py` | "The parser refuses, naming expected versus found" |
| Duplicate rejection | `show_duplicate_rejection.py` | "The key holds, and the log shows the split" |

Do not build these broken so you can fix them on camera. It is the wrong story -
it says they were afterthoughts - and "this already handles that" is a stronger
beat than "watch me patch this".

## State

Every script sets up, runs, and restores in a `finally`, so a failed demo does not
leave the repo broken in front of an audience. **`git status` should be clean after
every scenario.** Check it between takes.

## Fixtures

`fixtures/` holds saved HTML pages so no demo depends on the live site being up
or unchanged. They are shared with the test suite - the schema-drift fixture
serves both. Commit them.
