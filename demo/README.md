# Demo scenarios

A short script per scenario, each runnable in one command. Fumbling for a file
mid-demo undoes the effect.

`make demo-list` prints the menu. Every script runs from the committed archive with
no API key, narrates what it is about to do before doing it, checks that it showed
what it claims, and exits non-zero if it did not.

Full walkthrough and the reasoning behind each: [docs/phases/09-break-and-fix.md](../docs/phases/09-break-and-fix.md)

## Break and fix

| | Scenario | Run | Shows |
|---|---|---|---|
| D1 | A second process holds the DuckDB file | `make demo-d1` | The run retries, then fails with a line naming the holder's executable and PID, exit code 3. Nothing is written - the run log lives inside the locked file. Release, re-run, green |
| D2 | A failed API request (bad season id; 404, then the 401 the end of the month brings) | `make demo-d2` | The error names the endpoint and the status, never the URL or the key, is not retried, and writes nothing to the archive. Archived requests keep working with no key at all |
| D3 | A club's alias row is renamed so its matches stop mapping | `make demo-d3` | `all_clubs_mapped` fails naming the exact string (`93`), and the row count shows the 38 rows an inner join would have lost silently |
| D4 | A null injected into `rank_before` | `make demo-d4` | `features_not_null` fails naming the column and the count. XGBoost would have trained on it regardless - which is why the check stands in front of training |

D3 is the strongest of the four. In a real pipeline it fails *quietly* - the club
simply disappears - and silent data loss is the failure mode that actually bites
BI teams.

D1 depends on the lock strategy chosen in `usl/db.py`: retry with backoff, then a
message naming the holder. The demo also shows why the other strategy, write to a
temp file and swap, would not have helped: DuckDB refuses even a read-only open
while a writer holds the file.

## Demonstrate working, do not break

These are not staged failures. They are correct from day one and shown as such.
`make demo-working` runs all three.

| Behaviour | Script | Frame it as |
|---|---|---|
| Idempotency | `show_idempotency.py` | "Re-running is safe, and here is the log line that proves it" - the second load of the same season reports `inserted=0 updated=0 unchanged=380` and the attendance total does not move |
| Schema drift | `show_schema_drift.py` | "The parser refuses, naming expected versus found" - the fixture is the real payload with `homeID` and `awayID` deleted |
| Duplicate rejection | `show_duplicate_rejection.py` | "The key holds, and the log shows the split" - one match twice in a batch, again, then with a corrected gate: one row throughout |

Do not build these broken so you can fix them on camera. It is the wrong story -
it says they were afterthoughts - and "this already handles that" is a stronger
beat than "watch me patch this".

## State

Every script sets up, runs, and restores in a `finally`, so a failed demo does not
leave the repo broken in front of an audience:

- D1, D3 and D4 run the pipeline against a **scratch copy** of `data/usl.duckdb`
  in a temporary directory (built from the archive if the database does not exist
  yet). The real database is never opened.
- D3 edits the real `usl/ref/club_aliases.csv`, because that is the point, and
  restores it byte for byte. It checks `git status` on the file afterwards.
- D2 sets a fake key and stubs `requests.get` inside its own process only, and
  puts both back.
- The working-behaviour demos use a temporary or in-memory database.

**`git status` should be clean after every scenario.** Check it between takes.

## Fixtures

`fixtures/` holds the deliberately corrupted payload for the schema-drift demo. The
faithful responses live in `data/raw_archive/`, which is committed - so no demo
needs a live API call or a working subscription. See `fixtures/README.md`.
