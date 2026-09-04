# The build guide

This guide is a learning artifact, not a finished implementation. The modules under
`usl/` are stubs with signatures, docstrings, and TODO markers. You write the bodies.

**How to use it.** Each phase states its goal and its constraints, then poses an
exercise. Try the exercise first. Worked solutions live in collapsed `<details>`
blocks - open one only when you are stuck. Code inside a solution block is a sketch
to compare your thinking against, not a drop-in you paste. The point is that you
build it.

One exercise has no solution block at all. That is deliberate.

**Start with [phase 00](phases/00-data-access-and-the-clock.md)** whichever track you
take. The data source is a paid subscription running for a single month, and phase 00
is the one that stops that month being wasted.

---

## Two tracks

Same architecture in both. The MVP track takes the fastest defensible version of
every technical decision so you get something end to end quickly. The full track is
the version you show someone.

### [MVP track](mvp/) - an afternoon

| | |
|---|---|
| [00](mvp/README.md) | What the MVP cuts, and what it refuses to cut |
| [01](mvp/01-mvp-ingest-to-duckdb.md) | One season from the API into DuckDB with a real primary key |
| [02](mvp/02-mvp-sql-and-features.md) | Two SQL steps, league-wide rank, a thin feature set |
| [03](mvp/03-mvp-models.md) | Both models, default hyperparameters, MAE logged |
| [04](mvp/04-mvp-tableau.md) | CSV extracts into Tableau Public |
| [05](mvp/05-mvp-schedule.md) | Windows Task Scheduler, Tuesday morning |

### [Full track](phases/) - the portfolio build

| | | |
|---|---|---|
| [00](phases/00-data-access-and-the-clock.md) | Data access and the clock | The paid month, the archive rule, secrets |
| [01](phases/01-ingest-to-raw.md) | Ingest to raw | FootyStats client, schema drift, idempotency |
| [02](phases/02-duckdb-and-the-lock-problem.md) | DuckDB and the lock problem | Single-writer, run logging, write-to-temp-then-swap |
| [03](phases/03-club-name-consistency.md) | Club identity | Provider ids to your own, display names, the failing join |
| [04](phases/04-standings-as-of-match-date.md) | Standings as of match date | Point-in-time conference rank. The hardest SQL here |
| [05](phases/05-sql-layer.md) | The SQL layer | Three tiers, kept genuinely separate |
| [06](phases/06-features.md) | Features | Three families, the dead-rubber counterfactual, COVID |
| [07](phases/07-two-models.md) | Two models | One dataset, two feature lists, three output tables |
| [08](phases/08-tableau.md) | Tableau | Licensing reality, connector setup, the three views |
| [09](phases/09-break-and-fix.md) | The demo | Four break-and-fix scenarios, three working-behaviour demos |
| [10](phases/10-delivery.md) | Delivery | The framing, the video, the README |
| [11](phases/11-phase-two-dagster.md) | Phase two: Dagster | Deferred. Asset lineage and run history |
| [12](phases/12-phase-two-weather.md) | Phase two: weather | Deferred. Open-Meteo, stadium coordinates |

Phases 03 and 04 get their own docs because neither is a lookup. Club naming is a
mapping layer maintained by hand across nine seasons of rebrands. Standings is a
running calculation over match history. Both are usually underestimated and both
break things silently when they are wrong.

---

## Reference

- [Logging and run metadata](reference/logging-and-run-metadata.md) - what every run
  records, and why this is a feature rather than an afterthought
- [Tableau DuckDB connector setup](reference/tableau-duckdb-connector.md) - JDBC driver
  and `.taco` file, about fifteen minutes
- [Open questions](reference/open-questions.md) - decisions the guide leaves open, and
  where each one is resolved in this repo

---

## Conventions

- Solutions are collapsed. If a phase doc shows complete working code in its main
  body, that is a bug in the doc.
- Stub modules contain signatures and docstrings. `NotImplementedError` plus a TODO
  marker is the expected state of an unimplemented function.
- Two things ship working rather than staged as failures: **duplicate-match rejection**
  and **schema-drift detection**. They are demonstrated as correct behaviour in phase
  09. Do not build them broken so you can fix them on camera.
- No emoji, anywhere.
