# Phase 09 - Break and fix

**Goal.** Four failure scenarios you can run on demand, and three behaviours you
demonstrate working. The distinction between those two categories is the point of this
phase.

**Files.** `demo/`

---

## Two categories, and why the distinction matters

**Break and fix** is for failures the pipeline is designed to *surface*. You break
something, the pipeline tells you what broke, you fix it, it goes green. What is on
display is legibility.

**Demonstrate working** is for behaviours that are correct from day one. Idempotency
and schema drift detection are not staged failures. Do not build them broken so you can
fix them on camera - it is the wrong story (it says these were afterthoughts) and it is
also a worse demo, because "this already handles that" is stronger than "watch me
patch this".

Keep a `demo/` folder with the fixtures and a short script per scenario. Fumbling for a
file mid-demo undoes the effect.

---

## Break and fix, live

### D1 - The locked file

Open the DuckDB file in Tableau, then trigger the weekly run. It fails on the lock.
Show the log line naming Tableau, close Tableau, re-run, green.

*What it shows:* the failure is legible, not mysterious. The important half of this
demo is not that it failed - it is that six months from now the person reading that log
line knows immediately what to do.

*Script:* `demo/d1_locked_file.py`

*Note:* this scenario depends on the guard you built in
[phase 02](02-duckdb-and-the-lock-problem.md), the one with no solution block. What
exactly the demo shows depends on which strategy you chose - a retry demo and a
temp-file-swap demo look different and say different things. Whichever you built, be
able to say what happens in the other case.

### D2 - 404 source

Point a season URL at a dead path. The run shows a failed step with the HTTP status and
the URL. Fix, re-run.

*What it shows:* upstream failure surfaces as a failed asset, not as corrupt data. The
alternative - a scraper that catches the error, logs a warning, and returns an empty
DataFrame - produces a green run and a mart missing a season. That is the version that
gets shipped by accident.

*Script:* `demo/d2_dead_url.py`

*Watch for:* if your retry logic from
[phase 01](01-scrape-to-raw.md#exercise-13---fetch-politeness-and-caching) retries a
404, this demo takes five minutes of backoff before it fails. A 404 is not transient.

### D3 - The silent one

Edit a club name in `club_aliases.csv` so matches stop mapping. Run. The check catches
it and names the unmapped string.

*What it shows:* this is the best demo of the four, because in a real pipeline it fails
*quietly*. The club simply disappears, the row count drops by forty, and no error
fires. Silent data loss is the failure mode that actually bites BI teams, and it is
the one nobody demos because nobody instruments for it.

Show both signals: the check that names the unmapped string, and the row-count log line
that would have caught it even if the check had not.

*Script:* `demo/d3_club_rename.py`

### D4 - Null injection

Put a null into a feature column. Show whether the model handles it or the check flags
it, and explain which behaviour you chose and why.

*What it shows:* the answer to "what does your pipeline do with missing data" is a
decision you made, not an accident. Both answers are defensible. XGBoost handles nulls
natively by learning a default split direction; failing the run is stricter and safer.
What is not defensible is discovering on camera that you do not know which one happens.

*Script:* `demo/d4_null_injection.py`

---

## Demonstrate working, do not break

### Idempotency

Run Tuesday's job twice. Show the second run reporting zero inserted, N updated, and
attendance totals unchanged. This works out of the box by design - it is not staged as
a flaw. Frame it as: "re-running is safe, and here is the log line that proves it."

### Schema drift

Feed a saved HTML fixture with a renamed column. The parser raises, naming expected
versus found. Correct behaviour on display, not a bug being patched. Keep the fixture
in `demo/fixtures/` - it is a normal saved page with one column header edited.

### Duplicate rejection

Ingest the same fixture twice. The primary key holds, the log line shows the split.
Closely related to idempotency and worth showing as a separate beat because it is the
mechanism underneath it.

---

## Exercise 9.1 - Making the demos repeatable

Every scenario mutates state: D3 edits a checked-in CSV, D4 writes a null into a table,
D2 changes a URL. Make each one runnable twice in a row without leaving the repo dirty.

<details>
<summary>Solution</summary>

Each demo script sets up, runs, and restores, with the restore in a `finally` so a
failed demo does not leave the repo broken in front of an audience:

```python
def main() -> None:
    original = ALIASES.read_text(encoding="utf-8")
    try:
        ALIASES.write_text(original.replace("Tampa Bay Rowdies", "Tampa Bay Rowdiez"), encoding="utf-8")
        run_transform()          # expected to fail, loudly
    finally:
        ALIASES.write_text(original, encoding="utf-8")
```

Two refinements worth the effort. Snapshot the database file before a demo that writes
to it and restore afterwards, so D4 does not leave a null in your mart for the rest of
the session. And have each script print what it is about to do before doing it, so the
demo narrates itself and you are not talking over your own terminal.

`git status` should be clean after every scenario. Check it between takes.
</details>

---

## What "done" looks like

- Four scripts under `demo/`, each runnable in one command, each restoring state.
- Fixtures committed under `demo/fixtures/` so no demo depends on the live site.
- `make demo-list` prints the menu.
- You can run all four plus the three working-behaviour demos in under ten minutes.

Next: [phase 10 - Delivery](10-delivery.md).
