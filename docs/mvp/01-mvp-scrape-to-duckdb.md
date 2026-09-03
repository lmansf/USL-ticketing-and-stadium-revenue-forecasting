# MVP 01 - One season into DuckDB

**Goal.** One season of USL Championship matches in `raw_matches`, with a primary key
that makes re-running safe.

**Extends into:** [phase 01](../phases/01-scrape-to-raw.md),
[phase 02](../phases/02-duckdb-and-the-lock-problem.md)

---

## Pick a season

The most recent *complete* season. A complete season has attendance for every match,
which means every downstream step works without a null-handling detour on day one.

`usl/config.py` leaves `SEASONS` as a TODO. Set it to a single-element list for now.
Verify the season is actually available on the source before you commit to it - open
the page and look.

---

## Fetch and cache

Fetch once, cache to disk, parse from the cache. Twenty parse attempts should hit the
network once.

```python
path = CACHE_DIR / f"season_{season}.html"
if not path.exists():
    path.write_text(requests.get(url, headers=HEADERS, timeout=30).text, encoding="utf-8")
html = path.read_text(encoding="utf-8")
```

Set a real User-Agent that identifies you. The full track adds retry policy and
per-season cache invalidation; for one season neither earns its keep yet.

---

## Parse

Read the HTML yourself before writing the parser. The table structure is not something
this guide can tell you - confirm it on the live page, then write to what is there.

The one thing not to skip: assert on column *names*, not positions.

```python
EXPECTED = {"date", "home", "away", "score", "attendance"}
missing = EXPECTED - set(df.columns)
if missing:
    raise SchemaDriftError(f"missing {sorted(missing)}; found {sorted(df.columns)}")
```

Positional indexing works until the site adds a column, and then it reads the wrong
field forever without ever raising.

---

## Load

Two lines that matter:

```sql
CREATE TABLE IF NOT EXISTS raw_matches (
    match_id VARCHAR PRIMARY KEY,
    ...
);
```

and an upsert rather than an insert:

```sql
INSERT INTO raw_matches SELECT * FROM new_rows
ON CONFLICT (match_id) DO UPDATE SET
    attendance = excluded.attendance,
    score = excluded.score,
    scraped_at = excluded.scraped_at;
```

`match_id` is a hash of `season|date|home_raw|away_raw`. Attendance figures get
corrected after the fact, so updating on conflict is right - you want the latest, not
the first.

---

## Exercise M1.1 - Prove it

Run the load twice and demonstrate the second run changed nothing. What do you have to
log to be able to show that?

<details>
<summary>Solution</summary>

Row counts before and after are necessary but not sufficient - an upsert that
overwrites every row with identical values leaves the count unchanged too, and so does
one that overwrites them with wrong values.

The split is what you want: inserted, updated, unchanged. DuckDB does not hand you that
from `ON CONFLICT`, so compute it by comparing against the existing keys before the
write:

```python
existing = set(con.sql("SELECT match_id FROM raw_matches").df()["match_id"])
inserted = len(set(df["match_id"]) - existing)
updated = len(set(df["match_id"]) & existing)
log.info("raw_matches inserted=%s updated=%s total=%s", inserted, updated, len(df))
```

Second run should print `inserted=0 updated=N`. Add a total attendance sum on both
sides if you want the stronger check - identical sums plus zero inserts is hard to
argue with, and it is the demo in
[phase 09](../phases/09-break-and-fix.md#idempotency).
</details>

---

## The lock

DuckDB is single-writer. If you have the file open in Tableau or a DuckDB CLI, the
write fails.

**The MVP answer is: close it first.** That is a real answer for a laptop and a bad one
for anything scheduled, which is why
[phase 02](../phases/02-duckdb-and-the-lock-problem.md) makes solving it properly the
one exercise with no worked solution. When you get to scheduling in
[MVP 05](05-mvp-schedule.md), this stops being adequate.

---

## Done when

- `raw_matches` has one row per match for your chosen season.
- Running the load twice reports zero inserted and changes no attendance total.
- A fixture with a renamed column raises, naming the difference.

Next: [MVP 02 - SQL and features](02-mvp-sql-and-features.md).
