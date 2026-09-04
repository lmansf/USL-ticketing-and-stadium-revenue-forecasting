# Phase 01 - Ingest to raw

**Goal.** Every USL Championship season landed in `raw_matches`, exactly as the API
returned it, with a stable `match_id` that makes re-running safe.

**Read [phase 00](00-data-access-and-the-clock.md) first.** The subscription is a
30-day clock and the archive rule it describes constrains everything below.

**MVP cut.** One season, against the free `example` key. See
[docs/mvp/01-mvp-ingest-to-duckdb.md](../mvp/01-mvp-ingest-to-duckdb.md).

**Modules.** `usl/ingest/footystats.py`, `usl/ingest/archive.py`, `usl/load/raw.py`

---

## The source

[FootyStats](https://footystats.org/api) serves JSON over HTTP. Authentication is an
API key passed as a query parameter. The endpoints that matter here:

| Endpoint | Use |
|---|---|
| `league-list` | Find USL Championship and its season ids. Run once |
| `league-matches` | One season of matches with stats. The backbone |
| `league-table` | Published final table. A cross-check for [phase 04](04-standings-as-of-match-date.md), not a data source |
| match detail | Per-match record. **Undocumented** - see below |

You address a *season id*, not a year. `league-matches?season_id=1625` is the EPL
2018/19 season. The mapping from "USL Championship 2019" to its id is something you
discover from `league-list` and write into `usl/ref/seasons.csv`.

**Verify the response shape yourself.** Pull one season with the `example` key and read
the JSON before you write a parser. Do not trust a field list described in this guide
or generated for you - confirm it, then write to what is actually there. That
instruction applied to scraped HTML and it applies just as much to an undocumented
JSON endpoint.

---

## Why this is better than scraping, and where it is not

An API removes the two most annoying failure modes in the original design. Club
identity arrives as a stable id rather than a display string, which shrinks the
[phase 03](03-club-name-consistency.md) alias problem from "nine seasons of rebrands"
to "map the API's ids to your own". And the response is structured, so a changed field
is a `KeyError` rather than a column silently read from the wrong position.

What it adds is a bill and a deadline. Scraping worldfootball.net was free and could be
redone any time; this cannot. That is the trade, and it is worth it mainly because of
the archive rule - you pay once, archive, and own the data afterwards.

**There is no scraper.** The API is the only ingest path, which is worth stating
because it buys real simplicity: one source, one key format, one failure mode, no HTML
parsing, no reconciliation between two sources that share no key.

It also concentrates the risk. If the API is missing a field, there is no fallback - so
the attendance gate in
[phase 00](00-data-access-and-the-clock.md#the-gate-verify-attendance-before-you-pay)
runs before you subscribe, not after.

---

## Constraints

1. **Archive before you parse.** Raw response to disk, unparsed, before anything that
   could raise. This is [exercise 0.1](00-data-access-and-the-clock.md#exercise-01---archive-first-parse-second)
   and it is the difference between a project that survives the subscription lapsing
   and one that does not.

2. **Land it raw.** No cleaning, no type coercion, no renaming. One row per match as
   returned, plus `ingested_at` and `source_endpoint`. Cleaning happens in SQL where it
   is reviewable in a diff and re-runnable without re-requesting.

3. **Validate the shape by key name.** JSON removes the positional-column trap but not
   the drift problem. An undocumented endpoint can change its field set without notice
   and with no changelog to consult. Assert on the keys you require.

4. **Be idempotent.** Re-running Tuesday's job twice must not double any club's
   attendance. This works correctly from day one - it is not a demo failure, see
   [phase 09](09-break-and-fix.md#demonstrate-working-do-not-break).

5. **Never log the key.** It appears in every request URL. A logger that records full
   URLs at DEBUG will write a paid credential into `logs/`, which is one careless
   `git add -f` from being public.

---

## Exercise 1.1 - Schema drift guard

Write the parser so an unexpected response shape fails loudly, naming what it found
versus what it expected.

Think about the asymmetry before you open the solution: should a *missing* field and an
*extra* field be treated the same way?

<details>
<summary>Solution</summary>

```python
REQUIRED = {"id", "date_unix", "homeID", "awayID", "homeGoalCount", "awayGoalCount"}

def parse_season(payload: dict, season_id: int) -> pd.DataFrame:
    matches = payload["data"]          # confirm this envelope against a real response
    df = pd.DataFrame(matches)

    missing = REQUIRED - set(df.columns)
    extra = set(df.columns) - REQUIRED
    if missing:
        raise SchemaDriftError(
            f"season_id {season_id}: missing {sorted(missing)}; "
            f"found {sorted(df.columns)}"
        )
    if extra:
        log.debug("season_id %s: %d unrequired fields (kept)", season_id, len(extra))

    return df.assign(season_id=season_id, ingested_at=utcnow(),
                     source_endpoint="league-matches")
```

Missing raises. Extra does not even warn here, and that is a deliberate change from the
scraped-HTML version of this rule. An HTML table with an unexpected column suggested
the page had been restructured underneath you. A JSON API returning fields you do not
consume is just an API - FootyStats sends dozens per match, and warning on them would
make every run noisy enough that you stop reading the log.

Keep the extra fields on the raw frame rather than dropping them. Storage is free,
you are inside a 30-day window, and a field you discarded is a field you cannot get
back later. Filter at the staging tier, where it is reviewable.

The field names above are illustrative. Confirm them against a real response.
</details>

---

## Exercise 1.2 - Idempotency

Design a `match_id` that is stable across re-ingests, and make the load reject
duplicates rather than append them.

The API gives you its own match id. Decide whether to use it directly, and what you
lose either way.

<details>
<summary>Solution</summary>

Use the API's id as the natural key, but namespace it:

```python
df["match_id"] = "fs:" + df["id"].astype(str)
```

This is a genuine improvement over hashing `season|date|home|away` the way the scraped
version had to. That hash changed whenever the source renamed a club, which silently
turned updates into inserts. A provider id does not move when a club rebrands.

The `fs:` prefix costs nothing and buys optionality. If a second source is ever needed -
because the API turns out to be missing a field, or because you want to cross-check
attendance against a published figure - it gets its own namespace rather than colliding,
and an id in a log line says where it came from without a lookup.

Then load with an upsert, not an insert:

```sql
INSERT INTO raw_matches
SELECT * FROM new_rows
ON CONFLICT (match_id) DO UPDATE SET
    attendance     = excluded.attendance,
    home_goals     = excluded.home_goals,
    away_goals     = excluded.away_goals,
    ingested_at    = excluded.ingested_at;
```

with `match_id` as the primary key. Attendance gets corrected after the fact, so
updating on conflict is right - you want the latest figure, not the first one you saw.

Log the split every run: inserted, updated, unchanged. That count is your evidence the
guard works, and it is what you show in the demo.
</details>

---

## Exercise 1.3 - Rate limiting and the archive cache

The entry tier allows roughly 1800 requests an hour. That is generous, and it is not
the constraint that matters. Write the client so the constraint that *does* matter -
requests are finite and the window closes - shapes its behaviour.

<details>
<summary>Solution</summary>

Two mechanisms, and the second is the important one:

```python
def get(endpoint: str, **params) -> dict:
    path = archive_path(endpoint, params)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    if not API_KEY:
        raise NoSubscriptionError(
            f"{endpoint}{params} is not archived and no FOOTYSTATS_API_KEY is set. "
            "Every response this project needs should be under data/raw_archive/ - "
            "if this fires, something was never pulled during the subscription window."
        )

    _throttle()                        # simple sleep to stay well under the ceiling
    body = _get_with_retry(endpoint, {**params, "key": API_KEY})
    path.write_text(body, encoding="utf-8")
    return json.loads(body)
```

The throttle is trivial and mostly ceremonial at this volume - a fixed delay between
requests keeps you far below the ceiling and is politer than bursting.

The archive check is the real design. It means: during the month, you never spend a
request twice; after the month, every call is served from disk and the pipeline still
runs. `NoSubscriptionError` exists to make the one bad case loud - a code path asking
for something nobody archived, discovered after access is gone. Better a named
exception than a confusing 401.

Retry transient failures only. A 401 means the key is wrong or the subscription
lapsed, a 404 means the endpoint or season id is wrong, and neither improves with
waiting. Retrying them burns backoff time to deliver the same answer, and during a
30-day window that is not free. This is demo scenario D2 in
[phase 09](09-break-and-fix.md).
</details>

---

## What "done" looks like

- `python -m usl.run backfill` populates `raw_matches` for every season in
  `usl/ref/seasons.csv`.
- Running it again reports zero inserted, N updated, and no attendance total moves.
- A response with a missing required field raises `SchemaDriftError` naming both sides.
- **With `FOOTYSTATS_API_KEY` unset, the whole backfill still runs from the archive.**
- No log line, at any level, contains the key.
- `tests/test_footystats.py` and `tests/test_match_id.py` pass.

Next: [phase 02 - DuckDB and the lock problem](02-duckdb-and-the-lock-problem.md).
