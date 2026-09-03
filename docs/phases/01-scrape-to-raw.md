# Phase 01 - Scrape to raw

**Goal.** Nine seasons of USL Championship match-level attendance and results landed
in `raw_matches`, exactly as scraped, with a stable `match_id` that makes re-running
the job safe.

**MVP cut.** One season, no backfill loop, no retry policy. See
[docs/mvp/01-mvp-scrape-to-duckdb.md](../mvp/01-mvp-scrape-to-duckdb.md).

**Modules.** `usl/scrape/fetch.py`, `usl/scrape/parse.py`, `usl/load/raw.py`

---

## The source

worldfootball.net publishes per-season, per-match home attendance for USL
Championship, plus results and tables. One site, one parser shape, nine seasons.
Backheeled is a useful cross-check on current-season figures.

**Before you write the parser, open the page and read the HTML.** Season URLs and
table structure need verifying against the live site. Do not trust any structure
described in this guide, in `usl/scrape/parse.py`, or generated for you. Confirm it
yourself, then write to what is actually there.

`usl/config.py` leaves `SEASONS` as a TODO for exactly this reason - the available
range is something you verify, not something this guide asserts.

---

## Constraints

1. **Land it raw.** No cleaning, no type coercion, no renaming. One row per match, as
   scraped, plus `scraped_at` and `source_url`. Cleaning happens in SQL, where it is
   reviewable in version control and re-runnable without re-fetching.

2. **Be polite.** Nine seasons is a one-time backfill of a few thousand rows. Sleep
   between requests, set a real User-Agent that identifies you, and cache responses to
   disk during development so you are not re-hitting the site every time you fix a
   parse bug. The cache directory is gitignored; the fixtures you deliberately keep
   for tests go in `demo/fixtures/`.

3. **Validate the shape, not the position.** If the site adds or reorders a column and
   you index by position, you will silently read the wrong field forever. Assert on
   column names.

4. **Be idempotent.** Re-running Tuesday's job twice must not double any club's
   attendance. This needs to work correctly from day one. It is not a demo failure -
   see [phase 09](09-break-and-fix.md#demonstrate-working-do-not-break).

---

## Exercise 1.1 - Schema drift guard

Write the parser so that an unexpected set of columns fails loudly, with a message
naming what it found versus what it expected.

Think about the asymmetry before you open the solution: should a *missing* column and
an *extra* column be treated the same way?

<details>
<summary>Solution</summary>

```python
EXPECTED = {"date", "home", "away", "score", "attendance"}

def parse_season(html: str, season: int) -> pd.DataFrame:
    tables = pd.read_html(html)
    df = pick_match_table(tables)          # your selection logic
    df.columns = [normalize(c) for c in df.columns]

    missing = EXPECTED - set(df.columns)
    extra = set(df.columns) - EXPECTED
    if missing:
        raise SchemaDriftError(
            f"season {season}: missing {sorted(missing)}; "
            f"found {sorted(df.columns)}"
        )
    if extra:
        log.warning("season %s: new columns %s (ignored)", season, sorted(extra))

    return df.assign(season=season, scraped_at=utcnow(), source_url=url)
```

Missing columns raise. Extra columns only warn - the site adding a column should not
break your Tuesday run, but you want to know it happened. That asymmetry is the whole
design.

The error message naming both sides is what makes this useful six months later. A
bare `KeyError: 'attendance'` tells you nothing about what the page now looks like.
</details>

---

## Exercise 1.2 - Idempotency

Design a `match_id` that is stable across re-scrapes, and make the load reject
duplicates rather than append them.

Two sub-questions worth settling before you write anything. What is the natural key
of a match? And when the same match is scraped twice with *different* attendance
figures, which one wins?

<details>
<summary>Solution</summary>

Natural key: `season + date + home_club + away_club`. Hash it for a compact id.

```python
df["match_id"] = (
    df["season"].astype(str) + "|" +
    df["date"].astype(str) + "|" +
    df["home_raw"] + "|" + df["away_raw"]
).map(lambda s: hashlib.sha1(s.encode()).hexdigest()[:16])
```

Note this hashes the *raw* club strings, not canonical ids. The id has to be
computable at load time, before the alias mapping in
[phase 03](03-club-name-consistency.md) has run. The cost is that a club rename in the
source changes the `match_id` for that club's historical matches - worth knowing, and
worth writing down.

Then load with an upsert, not an insert:

```sql
INSERT INTO raw_matches
SELECT * FROM new_rows
ON CONFLICT (match_id) DO UPDATE SET
    attendance = excluded.attendance,
    score      = excluded.score,
    scraped_at = excluded.scraped_at;
```

with `match_id` as the primary key on `raw_matches`. Attendance gets corrected by
sources after the fact, so updating on conflict is right - you want the latest figure,
not the first one you saw.

Log the split every run: rows inserted, rows updated, rows unchanged. That count is
your evidence the guard is working, and it is what you show in the demo.
</details>

---

## Exercise 1.3 - Fetch politeness and caching

Write `fetch_season_html` so that a development loop of twenty parse attempts hits the
network once. Decide what invalidates the cache, and make the answer different for a
completed season than for the season currently in progress.

<details>
<summary>Solution</summary>

Key the cache by URL, store the raw bytes on disk, and gate on season status rather
than on a timestamp:

```python
def fetch_season_html(season: int, *, force: bool = False) -> str:
    path = CACHE_DIR / f"season_{season}.html"
    is_complete = season < CURRENT_SEASON
    if path.exists() and not force and is_complete:
        log.info("cache hit season=%s", season)
        return path.read_text(encoding="utf-8")
    html = _get_with_retry(season_url(season))
    path.write_text(html, encoding="utf-8")
    return html
```

A finished season never changes, so its cache entry never expires. The current season
changes weekly, so it is always re-fetched. A `force` flag covers the case where the
site corrects a historical figure.

The retry inside `_get_with_retry` is for transient network failures - connection
resets, 5xx. A 404 is not transient and must not be retried into silence; it should
surface as a hard failure naming the status and the URL. That is demo scenario D2 in
[phase 09](09-break-and-fix.md).
</details>

---

## What "done" looks like

- `python -m usl.run backfill` populates `raw_matches` for every configured season.
- Running it a second time reports zero inserted and N updated, and no attendance
  total changes.
- Feeding a fixture with a renamed column raises `SchemaDriftError` naming expected
  versus found.
- `tests/test_parse.py` and `tests/test_match_id.py` pass.

Next: [phase 02 - DuckDB and the lock problem](02-duckdb-and-the-lock-problem.md).
