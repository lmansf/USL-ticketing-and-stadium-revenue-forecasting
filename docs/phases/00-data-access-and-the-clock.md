# Phase 00 - Data access and the clock

**Goal.** Every row you will ever need, pulled and archived to disk, before the
subscription lapses.

**Read this before you subscribe.** It is the phase that constrains every other one,
and the mistakes it prevents are the unrecoverable kind.

**Files.** `usl/ingest/footystats.py`, `usl/ingest/archive.py`, `.env.example`

---

## The source

[FootyStats](https://footystats.org/api) publishes a JSON API covering USL
Championship. Paid subscription, roughly 30 EUR per month on the entry tier, and the
plan here is **one month only**.

That single fact reorganises the project.

---

## There are now two clocks, and they run in the wrong order

The guide as originally written had one deadline: the 14-day Tableau Desktop trial, at
step 9, with everything before it free and unlimited. That is no longer true.

| Clock | Length | When | What lapsing costs you |
|---|---|---|---|
| FootyStats subscription | ~30 days | **First** | The data. Permanently, unless archived |
| Tableau Desktop trial | 14 days | Later | The live connection. The data is untouched |

The Tableau clock is the forgiving one. When it expires you lose a *connection*, and
your CSV extracts still open in the free edition. The FootyStats clock is not
forgiving: when it expires you lose *access to the source*, and no amount of later
work rebuilds a season you never pulled.

So the order inverts. Data acquisition moves from "step 1 of a leisurely sequence" to
"the thing with a deadline, done first and done completely."

**Do not start both clocks in the same month unless you have to.** Fourteen days of
Tableau inside thirty days of FootyStats is survivable but tight, and it means the two
weeks you most want to spend on the dashboard are the two weeks you are also still
worrying about backfill gaps. Pull and archive the data, let the subscription lapse,
then start Tableau against the archive.

---

## The rule that saves the project

**Archive every raw API response to disk, unparsed, before you do anything else with
it.**

Not the DuckDB tables - those are derived, and you will rebuild them a hundred times as
you fix the SQL. The raw JSON, exactly as the API returned it, written to
`data/raw_archive/` and committed.

This inverts something the scaffold used to say. `data/usl.duckdb` is still a build
product you can delete freely. But once the subscription lapses, the raw archive is the
only copy of the source data in existence for you, and it cannot be regenerated at any
price short of another 30 EUR. Treat it the way you would treat the only copy of a
dataset someone collected by hand, because functionally that is what it is.

Concretely, this means the archive directory is **not** gitignored, and the ingest step
writes to it before parsing rather than after. A parse bug that crashes the run must
still leave the response on disk - otherwise you burn a request, learn nothing, and
have nothing to debug against.

### Exercise 0.1 - Archive first, parse second

Design the ingest step so that a crash anywhere in parsing still leaves the raw
response on disk, and so that re-running does not re-request anything already archived.

<details>
<summary>Solution</summary>

Write the response body to disk the moment it arrives, before it is handed to anything
that could raise:

```python
def fetch_and_archive(endpoint: str, params: dict) -> dict:
    path = archive_path(endpoint, params)
    if path.exists():
        log.info("archive hit %s", path.name)
        return json.loads(path.read_text(encoding="utf-8"))

    body = _get(endpoint, params)          # raw text, no parsing yet
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")   # durable BEFORE anything can fail
    log.info("archived %s (%d bytes)", path.name, len(body))
    return json.loads(body)
```

The `path.exists()` check is doing two jobs. During the subscription it stops you
re-spending requests on data you already hold. After the subscription lapses it is the
*only* path - every call is an archive hit, and the pipeline keeps working forever
against a dead key.

That second property is worth building for deliberately: the finished repo should run
end to end for someone who has no FootyStats subscription at all, which is most people
who will look at it.

Two refinements. Include the endpoint and the meaningful parameters in the filename so
the archive is browsable and the cache key is obvious on sight - `league-matches` plus
the season id, not a hash. And keep the API key out of both the filename and the file:
it is a paid credential, and the archive is going into git.
</details>

---

## The API key is a paid credential

It goes in a `.env` file, which `.gitignore` already covers, and it is read via
`python-dotenv`. `.env.example` is committed and holds the variable name with no value.

Say it plainly because the failure is expensive rather than embarrassing: a key
committed to a public repo is someone else's month of requests on your card. Check
`git status` before your first commit after subscribing, and if you do leak it, rotate
it in the FootyStats settings rather than deleting the commit - the commit survives in
forks and caches.

The archive files go into git. **The key does not.** Keep that distinction sharp,
because they travel together in your working directory.

---

## Build the client before you pay

FootyStats accepts the literal key `example`, which serves the English Premier League
2018/19 season (season id `1625`). That is a complete, real season of the same response
shape you will get for USL.

So the entire client - auth, retry, rate limiting, archiving, parsing, the schema
guard, the loader, and most of `stg_matches` - can be built, tested, and debugged
against the example key for nothing, before your subscription starts. Do that first.
The day you subscribe should be the day you point a finished client at a different
season id, not the day you start writing one.

It also means `tests/test_footystats.py` can run against a committed example-key
fixture forever, and that a reader who clones this repo can exercise the ingest path
without paying anything.

### Exercise 0.2 - Plan the month

Before subscribing, write down the full list of requests you intend to make and what
each one is for. Estimate the total.

<details>
<summary>Solution</summary>

The entry tier allows on the order of 1800 requests an hour, so the constraint is
almost certainly not rate. It is *knowing what to ask for*.

The list is roughly:

- One `league-list` call, to find the USL Championship entry and its season ids. You
  cannot ask for "2019" - you ask for a season id, and the mapping from year to id is
  something you have to discover and write down.
- One `league-matches` call per season. Nine or ten calls, and this is the backbone.
- Possibly one match-detail call per match if attendance is not on the league-matches
  response - see the open question below. That is the order of a few thousand calls,
  which the rate limit accommodates comfortably but which you want to run once,
  archived, not repeatedly.
- One `league-table` call per season, as a cross-check on your reconstructed standings
  rather than as a data source. Cheap, and it turns phase 04 from "I think this is
  right" into "this matches the published table".

Write the season-id mapping into `usl/ref/seasons.csv` as you discover it. It is the
same class of artefact as `club_aliases.csv` - small, hand-maintained, load-bearing,
and impossible to reconstruct later without the subscription.

The thing to avoid is discovering in week four that you needed a field you never
requested. Hence: pull broadly, archive everything, filter later. Requests are cheap
inside the month and infinitely expensive outside it.
</details>

---

## The gate: verify attendance before you pay

**Attendance is the target variable.** Everything else in this project is a feature.
There is no scraper any more and no second source - if the API does not carry
per-match attendance for USL, there is nothing to predict and the project does not
exist in its current form.

So this is a gate, not an open question, and it is cheap to pass through. Run it before
you subscribe:

```
python scripts/check_attendance_coverage.py
```

That is a real working script, not a stub. It uses the free `example` key and the EPL
2018/19 season to answer the weak question: does an attendance field exist at all,
anywhere in this API. If it does not, stop - you need a different provider, and you
have spent nothing finding that out.

Then, on day one of the subscription, run it against a real USL season:

```
python scripts/check_attendance_coverage.py --season-id <a USL season id>
```

**That second run is the one that matters, and the first does not substitute for it.**
Gate figures are far better covered for major European leagues than for anywhere else,
so an EPL pass tells you the schema supports attendance, not that FootyStats holds it
for USL Championship in 2017.

### Three outcomes, not two

The dangerous one is in the middle.

| Outcome | What to do |
|---|---|
| Field absent | Try the undocumented match-detail endpoint, which returns more per-match fields than the season listing. If it is absent there too, this provider cannot support the project |
| Present, well populated | Proceed. This is the case the repo is built for |
| **Present, mostly empty** | The trap. A naive check reports success |

That middle case is the likely one and it is why the script reports a *share* and a
median rather than a yes or no. APIs signal "no data" as `0`, `-1`, `""`, `null` and
`"N/A"` more or less interchangeably, so a field that is technically present can be six
thousand zeroes. Sanity-check the median against what you know these clubs actually
draw - USL gates run in the low thousands, so a median of 0, or a suspiciously round
number, means placeholder rather than data.

The script treats anything below 80 percent populated as unusable and exits non-zero.
Adjust that threshold if you like, but decide it deliberately rather than eyeballing.

### If the gate fails

The scraper was deleted in favour of the API, which was the right call for a single
clean ingest path. It is still in git history, and the script prints the recovery
commands:

```
git log --oneline --diff-filter=D -- usl/scrape/
git checkout <sha>^ -- usl/scrape/ tests/test_parse.py
```

Recovering it means going back to two sources joined on `season + date + club`, because
the API and worldfootball.net share no key. That is real work, and it is worth knowing
you might face it *before* the subscription starts rather than in week three.

The honest framing: running this check costs nothing and takes a minute. Subscribing
first and checking later costs thirty euros and a month.

## What "done" looks like

- `.env` holds your key; `git status` shows it untracked.
- The client runs green against the `example` key with no subscription.
- `data/raw_archive/` holds one file per request, committed.
- `usl/ref/seasons.csv` maps each season year to its FootyStats season id.
- Attendance coverage is resolved in writing, and the ingest path you are not using is
  deleted.
- The pipeline runs end to end with the key removed from `.env`, entirely from the
  archive.

That last one is the acceptance test for this whole phase. If it passes, the
subscription can lapse and the project survives.

Next: [phase 01 - Ingest to raw](01-ingest-to-raw.md).
