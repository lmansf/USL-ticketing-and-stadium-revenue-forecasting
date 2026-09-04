# MVP 01 - One season into DuckDB

**Goal.** One season of matches in `raw_matches`, from the FootyStats API, with a
primary key that makes re-running safe.

**Extends into:** [phase 00](../phases/00-data-access-and-the-clock.md),
[phase 01](../phases/01-ingest-to-raw.md),
[phase 02](../phases/02-duckdb-and-the-lock-problem.md)

---

## Start with the free key, not your subscription

FootyStats accepts the literal key `example`, which serves the English Premier League
2018/19 season (season id `1625`). That is a complete, real season with the same
response shape you will get for USL.

**Build the entire MVP against it.** Client, archive, parser, loader, SQL, both models,
the CSV export - all of it works on one season of EPL data, and none of it costs you a
day of subscription. When you subscribe, you change a season id and a key, and
everything downstream already works.

This is not a toy shortcut. It is the sequencing the full track uses too, for the same
reason: the day you start paying should be the day you point a finished client at a
different season, not the day you start writing one.

```
cp .env.example .env
# FOOTYSTATS_API_KEY stays empty for now
```

---

## Archive before you parse

The one rule from [phase 00](../phases/00-data-access-and-the-clock.md) that the MVP
does not get to skip.

```python
path = ARCHIVE_DIR / f"league-matches_season_{season_id}.json"
if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
else:
    body = requests.get(url, params={**params, "key": key}, timeout=30).text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")   # durable BEFORE anything can fail
    payload = json.loads(body)
```

Write the response to disk before deserialising it, so a malformed payload is still
there to debug against. And note what the `path.exists()` branch means once the
subscription is over: every call is an archive hit, and the pipeline keeps running
forever against a key that no longer works.

`data/raw_archive/` is committed to git. That is the opposite of the rule for
`data/usl.duckdb`, and it is deliberate - the database is rebuildable, the archive is
not.

---

## Never commit the key

It goes in `.env`, which is gitignored. It is a paid credential: a key in a public repo
is someone else's month of requests on your card.

It also appears in every request URL, so do not log full URLs. `logs/` is gitignored,
but one careless `git add -f` is all it takes.

---

## Parse

Read a real response before writing the parser. Pull one season with the example key,
open the JSON, and write to what is actually there rather than to any field list
described in this guide.

The one thing not to skip: assert on field names.

```python
REQUIRED = {"id", "date_unix", "homeID", "awayID", "homeGoalCount", "awayGoalCount"}
missing = REQUIRED - set(df.columns)
if missing:
    raise SchemaDriftError(f"missing {sorted(missing)}; found {sorted(df.columns)}")
```

Keep the fields you do not need rather than dropping them. FootyStats sends dozens per
match, storage is free, and inside a 30-day window a discarded field is one you cannot
get back later.

---

## Load

```sql
CREATE TABLE IF NOT EXISTS raw_matches (
    match_id VARCHAR PRIMARY KEY,
    ...
);
```

with an upsert rather than an insert:

```sql
INSERT INTO raw_matches SELECT * FROM new_rows
ON CONFLICT (match_id) DO UPDATE SET
    attendance  = excluded.attendance,
    ingested_at = excluded.ingested_at;
```

`match_id` is `"fs:" + str(provider_id)`. Using the provider's own id beats hashing
`season|date|home|away`: the hash moved whenever a club was renamed, which silently
turned updates into inserts. The `fs:` prefix keeps room for a second source without
collisions.

---

## Exercise M1.1 - Prove it

Run the load twice and demonstrate the second run changed nothing. What do you have to
log to be able to show that?

<details>
<summary>Solution</summary>

Row counts before and after are necessary but not sufficient - an upsert that
overwrites every row with identical values leaves the count unchanged, and so does one
that overwrites them with wrong values.

The split is what you want: inserted, updated, unchanged. DuckDB does not hand you that
from `ON CONFLICT`, so compute it against the existing keys before the write:

```python
existing = set(con.sql("SELECT match_id FROM raw_matches").df()["match_id"])
inserted = len(set(df["match_id"]) - existing)
updated = len(set(df["match_id"]) & existing)
log.info("raw_matches inserted=%s updated=%s total=%s", inserted, updated, len(df))
```

Second run prints `inserted=0 updated=N`. Add a total attendance sum on both sides for
the stronger check - identical sums plus zero inserts is hard to argue with, and it is
the demo in [phase 09](../phases/09-break-and-fix.md#idempotency).

Note the API makes this easier than the scraped version was. A provider id is stable, so
re-ingesting genuinely cannot produce a duplicate unless your key derivation is wrong.
</details>

---

## Exercise M1.2 - Run it with the key removed

Empty `FOOTYSTATS_API_KEY` in `.env` and run the ingest again. It should succeed.

<details>
<summary>Solution</summary>

If it succeeds, everything came from `data/raw_archive/` and you have just proved the
property that matters most: this project survives its own subscription lapsing.

If it fails, one of two things is wrong. Either the archive check runs after the fetch
rather than before it, or something in the path is asking for a request nobody
archived. Both are worth fixing now, while you can still make the request, rather than
in five weeks when you cannot.

Make this a habit rather than a one-off. Running with the key unset is the acceptance
test for the whole data-access phase, and the only time it is cheap to fail is while
you still have access.
</details>

---

## The lock

DuckDB is single-writer. If you have the file open in Tableau or a DuckDB CLI, the
write fails.

**The MVP answer is: close it first.** A real answer for a laptop and a bad one for
anything scheduled, which is why
[phase 02](../phases/02-duckdb-and-the-lock-problem.md) makes solving it properly the
one exercise with no worked solution. When you get to [MVP 05](05-mvp-schedule.md),
this stops being adequate.

---

## Done when

- `raw_matches` holds one row per match for the example season.
- `data/raw_archive/` holds the response, and it is committed.
- Running the load twice reports zero inserted and moves no attendance total.
- The ingest runs green with `FOOTYSTATS_API_KEY` empty.
- `git status` does not show `.env`.

Next: [MVP 02 - SQL and features](02-mvp-sql-and-features.md).
