# Tests

Tests for the transformations with obvious correctness criteria. They describe
what correct looks like, and the implementations under `usl/` satisfy them.

**The suite is green on a fresh clone.** It started red by design - every test was
a stub that failed with `NotImplementedError` or skipped with a TODO naming the doc
that explained it - and the guide's last phase says to turn that section of the
README around once the stubs are implemented. They are, so it has been. No test
skips.

```
make test                       # or: python -m pytest
python -m pytest tests/test_standings.py -v
make check                      # lint + typecheck + test, the one command a reviewer runs
```

## What is covered, and why these

Not everything is testable at reasonable cost. These are the transformations
where correctness has a clear, checkable definition:

| File | Covers | Why it earns a test |
|---|---|---|
| `test_footystats.py` | Archive-before-parse, key handling, retry classes, schema drift | Runs off the committed example-key fixture, no subscription needed |
| `test_match_id.py` | `match_id` stability and uniqueness, both namespaces | Pure function of the provider id, or of four fields |
| `test_club_mapping.py` | Normalisation, unmapped detection, the row-count second signal | The silent failure mode - worth pinning down |
| `test_standings.py` | Point-in-time correctness, tie-breaking, rank scope, the real EPL table | Hand-checkable against a small fixture, and externally checkable against a published table |
| `test_features.py` | Lag windows, stakes arithmetic, definitions/columns agreement | Leakage is invisible without a test |
| `test_idempotency.py` | Insert/update/unchanged split, duplicate rejection | The behaviour demonstrated in phase 09 |
| `test_sql_layer.py` | Model order, rebuild idempotency, stop-between-tiers, every check logged | Cheap, catches ordering mistakes |
| `test_models.py` | Chronological split, importance reindexing, upsert-on-rerun, the naive baseline, CV and variance tables, export | Each has a specific trap |
| `test_run_log.py` | Run and check logging, key redaction | Logging is a feature, so it gets tests |
| `test_db_lock.py` | The lock guard: held lock, killed writer, non-lock errors, retry-then-succeed | Was the one unguided exercise; its test was part of it |

## What is not covered

- **Live API calls.** Tested against committed archive fixtures instead, with the
  HTTP layer faked. A test that spends a request against a metered subscription is
  a test you will disable.
- **Model accuracy.** There is no assertion to make. A test that pins MAE below a
  threshold fails the day the data changes, for no reason.
- **Tableau.** Nothing to assert against.
- **Cross-process readers during a write.** DuckDB refuses a second process the file
  while a writer holds it, which is the lock scenario, and it is tested from the
  writer's side.

## Fixtures

`conftest.py` provides an in-memory DuckDB connection and small hand-built frames:
`tiny_season` (four clubs, six matches, final table worked out in the docstring),
`tiny_clubs`, `club_aliases`, `tiny_structure`, `tiny_derbies`, `tiny_raw` (the same
season in the shape of `raw_matches`), and the helper `stage_frames`, which stands up
the staging tier and the reference tables straight from those frames so a standings
test can materialise `int_standings` without an alias CSV or an archive on disk.

The one real fixture is `data/raw_archive/league-matches_season_id_1625.json`, the
example-key EPL 2018/19 response, which is committed - so the ingest tests and the
published-table test run for anyone, forever, with no key. Its 380 matches and its
final table are numbers you can check against the published season rather than
against your own parser.

There are no HTML fixtures - there is no scraper, and the API is JSON.
