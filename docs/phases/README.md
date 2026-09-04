# Full track

The portfolio build. Each phase states its goal and its constraints, poses an
exercise, and keeps worked solutions in collapsed `<details>` blocks.

Every phase names its MVP cut, so you can see what the [fast track](../mvp/)
leaves out and what that costs.

| | Phase | Covers |
|---|---|---|
| [00](00-data-access-and-the-clock.md) | Data access and the clock | The paid month, the archive rule, secrets. **Read first** |
| [01](01-ingest-to-raw.md) | Ingest to raw | FootyStats client, schema drift, idempotency |
| [02](02-duckdb-and-the-lock-problem.md) | DuckDB and the lock problem | Single-writer, run logging, the unguided exercise |
| [03](03-club-name-consistency.md) | Club name consistency | Nine seasons of rebrands, relocations, folds |
| [04](04-standings-as-of-match-date.md) | Standings as of match date | Point-in-time conference rank. The hardest SQL here |
| [05](05-sql-layer.md) | The SQL layer | Three tiers, checks between them |
| [06](06-features.md) | Features | Three families, the dead-rubber counterfactual, COVID |
| [07](07-two-models.md) | Two models | One dataset, two feature lists, three output tables |
| [08](08-tableau.md) | Tableau | Licensing, connector setup, the three views |
| [09](09-break-and-fix.md) | The demo | Four break-and-fix, three working-behaviour |
| [10](10-delivery.md) | Delivery | Framing, video, README |
| [11](11-phase-two-dagster.md) | Phase two: Dagster | Deferred |
| [12](12-phase-two-weather.md) | Phase two: weather | Deferred |

## Build order

Not the same as the reading order, and it is now shaped by two deadlines rather
than one. The full ordering, with what is free and what is on a clock, is in the
[top-level README](../../README.md#build-order).

The short version:

1. **Before subscribing, free.** Build the whole ingest client against the
   FootyStats `example` key. Get one season through `stg_matches`.
2. **During the paid month.** Season ids, the attendance question, the full
   backfill, league tables as a cross-check. Archive everything, then verify the
   pipeline runs with the key removed.
3. **After it lapses, free again.** Staging, `int_standings` (the hardest SQL,
   before the fun parts), mart, both models, extracts, the weekly task.
4. **Then** the 14-day Tableau trial, then record and send.

The FootyStats clock comes first and is the unforgiving one - lapsing costs you
the data permanently unless it is archived. The Tableau clock only costs you a
live connection. Do not run them in the same month if you can avoid it.
