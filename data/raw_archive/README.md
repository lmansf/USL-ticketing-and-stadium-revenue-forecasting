# Raw archive

Every response the FootyStats API ever returned to this project, unparsed, exactly
as received.

**This directory is committed to git, and that is deliberate.** It is the one place in
the repo where that rule applies to data.

## What is here

| File | What | Rows |
|---|---|---|
| `league-matches_season_id_1625.json` | EPL 2018/19, the season the free `example` key serves. Pulled 2026-09-05 | 380 matches, attendance on all 380 |

That single file is what the whole pipeline runs from today, and what every test and
demo uses. The USL seasons land here during the subscription month, one file per
`league-matches` request (plus `league-tables_season_id_<id>.json` per season as a
standings cross-check, and paged responses as `..._page_2.json` and so on).

## Why

The FootyStats subscription runs for one month. When it lapses, this directory is the
only copy of the source data in existence for this project, and it cannot be
regenerated at any price short of another subscription. `data/usl.duckdb` is a build
product you can delete freely and rebuild from here in seconds; the reverse is not
true.

Treat these files the way you would treat the only copy of a dataset someone collected
by hand, because functionally that is what they are.

## The acceptance test

With `FOOTYSTATS_API_KEY` unset or empty, the full pipeline must run end to end from
this directory alone:

```
make backfill
make transform
make train
make export
```

If that passes, the subscription can lapse and the project survives. It is also what
makes this repo runnable by someone who has never paid FootyStats anything, which is
most people who will look at it. It passes today.

## Rules

- **Written before parsing, committed after.** A fresh body lands in
  `<file>.partial`, is parsed, and only a body that is JSON and does not say
  `"success": false` replaces the archived file, in one atomic rename. A body that
  fails either check is moved to `<file>.bad` and kept for you to look at, but is
  never served as a hit and never overwrites what was there. So a lapsed-key error
  envelope or a captive-portal page cannot take the place of a season, even with
  `--force`. `.partial` and `.bad` files are gitignored; `python -m usl.run archive`
  counts the quarantined ones.
- **One file per pull of a live season.** A season still being played is
  re-requested every week, and each pull is archived on its own as
  `league-matches_season_id_<id>_as_of_<date>.json`. Nothing is overwritten and the
  history of what the API said on each date is kept. Without a key the newest
  snapshot is served, with a warning that nothing is being refreshed.
- **Byte for byte.** Not pretty-printed, not re-encoded. What is on disk is what the
  API said.
- **No API key.** Not in a filename, not inside a file. These go into git; the key
  does not. `archive_path()` strips the key before it builds the name.
- **Readable filenames.** `<endpoint>_<param>_<value>.json` with the parameters
  sorted - `league-matches_season_id_1625.json`, not a hash. You will need to browse
  this while the clock is running to work out what you have not pulled yet;
  `python -m usl.run archive` summarises it.

See [docs/phases/00-data-access-and-the-clock.md](../../docs/phases/00-data-access-and-the-clock.md).
