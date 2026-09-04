# Raw archive

Every response the FootyStats API ever returned to this project, unparsed, exactly
as received.

**This directory is committed to git, and that is deliberate.** It is the one place in
the repo where that rule applies to data.

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
```

If that passes, the subscription can lapse and the project survives. It is also what
makes this repo runnable by someone who has never paid FootyStats anything, which is
most people who will look at it.

## Rules

- **Written before parsing.** A malformed payload still lands here, so a parse bug
  costs you a debugging session rather than a request.
- **Byte for byte.** Not pretty-printed, not re-encoded. What is on disk is what the
  API said.
- **No API key.** Not in a filename, not inside a file. These go into git; the key
  does not.
- **Readable filenames.** `league-matches_season_1625.json`, not a hash. You will need
  to browse this while the clock is running to work out what you have not pulled yet.

See [docs/phases/00-data-access-and-the-clock.md](../../docs/phases/00-data-access-and-the-clock.md).
