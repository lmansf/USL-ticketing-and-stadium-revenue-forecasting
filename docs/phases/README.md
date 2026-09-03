# Full track

The portfolio build. Each phase states its goal and its constraints, poses an
exercise, and keeps worked solutions in collapsed `<details>` blocks.

Every phase names its MVP cut, so you can see what the [fast track](../mvp/)
leaves out and what that costs.

| | Phase | Covers |
|---|---|---|
| [01](01-scrape-to-raw.md) | Scrape to raw | Schema drift, idempotency, politeness, caching |
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

Not the same as the reading order. Phases 01 through 07 build in sequence;
`int_standings` (phase 04) is the hardest SQL and belongs before the fun parts.

1. Scrape one season, print the DataFrame. Nothing else.
2. Backfill all nine into DuckDB with the idempotency guard.
3. Staging plus club aliases, failing loudly on unmapped names.
4. `int_standings`.
5. Mart plus features.
6. Both models, all three output tables.
7. Tableau extracts.
8. Schedule the weekly task and let it run for two weeks so you have real history.
9. **Then** start the Tableau Desktop trial.
10. Record, write the delivery email, send.

Steps 1 through 8 are free and unlimited. Step 9 is a 14-day clock. Do not start
it early.
