# Tests

Test stubs for the transformations with obvious correctness criteria. They
describe what correct looks like; the implementations under `usl/` are yours to
write.

**The suite starts red.** That is the intended state of a fresh clone. Every test
here fails with `NotImplementedError` until you implement the thing it covers, and
each one names the doc that explains it.

```
make test                       # or: python -m pytest
python -m pytest tests/test_standings.py -v
python -m pytest -m "not slow"
```

## What is covered, and why these

Not everything is testable at reasonable cost. These are the transformations
where correctness has a clear, checkable definition:

| File | Covers | Why it earns a test |
|---|---|---|
| `test_footystats.py` | Archive-before-parse, key handling, schema drift | Runs off a committed example-key fixture, no subscription needed |
| `test_match_id.py` | `match_id` stability and uniqueness | Pure function of four fields |
| `test_club_mapping.py` | Normalisation, unmapped detection | The silent failure mode - worth pinning down |
| `test_standings.py` | Point-in-time correctness, tie-breaking, rank scope | Hand-checkable against a small fixture |
| `test_features.py` | Lag windows, definitions/columns agreement | Leakage is invisible without a test |
| `test_idempotency.py` | Insert/update/unchanged split | The behaviour demonstrated in phase 09 |
| `test_sql_layer.py` | Model order, rebuild idempotency | Cheap, catches ordering mistakes |
| `test_models.py` | Chronological split, importance reindexing | Both have specific traps |
| `test_run_log.py` | Run and check logging | Logging is a feature, so it gets tests |

## What is not covered

- **Live API calls.** Tested against committed archive fixtures instead. A test that
  spends a request against a metered subscription is a test you will disable.
- **Model accuracy.** There is no assertion to make. A test that pins MAE below a
  threshold fails the day the data changes, for no reason.
- **The DuckDB lock guard.** Deliberately. It is the one unguided exercise, and
  writing its test is part of it - `test_db_lock.py` is a stub with the scenarios
  to cover and no assertions.
- **Tableau.** Nothing to assert against.

## Fixtures

`conftest.py` provides an in-memory DuckDB connection and small hand-built frames.

API fixtures come from `data/raw_archive/`, which is committed - so the ingest tests
run for anyone, forever, with no key. Pull one `example`-key season (EPL 2018/19,
season id 1625) and the fixture exists. Its 380 matches are a row count you can check
against the published season rather than against your own parser.

There are no HTML fixtures - there is no scraper, and the API is JSON.
