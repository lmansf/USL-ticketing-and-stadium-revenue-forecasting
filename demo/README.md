# Demo scenarios

Fixtures and a short script per scenario. Fumbling for a file mid-demo undoes the
effect.

`make demo-list` prints the menu.

Full walkthrough and the reasoning behind each: [docs/phases/09-break-and-fix.md](../docs/phases/09-break-and-fix.md)

## Break and fix

| | Scenario | Script | Shows |
|---|---|---|---|
| D1 | Locked DuckDB file produces a stale run | `d1_locked_file.py` | The failure is legible, not mysterious |
| D2 | Failed API request (bad season id, or a 401 when the month ends) | `d2_dead_url.py` | Upstream failure surfaces as a failed asset, not corrupt data |
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

`fixtures/` holds deliberately corrupted payloads for the schema-drift demo. The
faithful ones live in `data/raw_archive/`, which is committed - so no demo needs a
live API call or a working subscription. Commit both.
