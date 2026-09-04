# USL Attendance Forecasting

A working demand model on nine seasons of USL Championship match data, and a
measurement framework for the promotion-and-relegation effect that arrives in 2028.

**Headline question: does league position predict attendance?**

The project answers it by training two models on one dataset. Model A is blind to
the table. Model B sees conference rank and the stakes features derived from it.
They share every upstream table and split only at the model layer, so any
difference in error is attributable to those features and nothing else.

---

## The thesis

USL owners voted in promotion and relegation. USL Premier launches as Division One
in 2028, targeting 20 clubs, with pro-rel between tiers.

That breaks every attendance forecast the league owns. In a closed league, demand is
a function of the schedule. Under pro-rel, demand becomes a function of table
position - a club fighting relegation in September sees something no American soccer
dataset has ever recorded. Nobody has this data. Forecasts will be built on
assumptions borrowed from Europe.

So the claim here is narrow and honest:

> Here is a working demand model on nine seasons of real USL data, and here is a
> measurement framework for the pro-rel effect, instrumented now, so that when
> relegation arrives there is a baseline to compare against.

**What this can prove:** a model that predicts match attendance and stays accurate
week over week.

**What this cannot prove:** that relegation pressure moves attendance. It has not
happened yet. Saying so plainly is a stronger move than pretending otherwise. See
[the honesty note](docs/phases/06-features.md#the-honesty-note) for exactly where the
line falls between measured, proxied, and instrumented-but-unvalidated.

---

## Architecture

```
                    FootyStats API (paid, 1 month)
                              |
                      [ usl/ingest/ ]  archive every response BEFORE parsing
                              |
                              v
            data/raw_archive/ ........... committed to git. The only copy of the
                              |           source data once the subscription lapses
                              v
  RAW        raw_matches ................. exactly as returned, never edited
                              |
                              |  club_aliases.csv  (checked in, load-bearing)
                              v
  STAGING    stg_clubs, stg_matches ...... types, canonical club_id, one row per match
                              |
                              v
  INTERMEDIATE  int_standings ............ conference rank as of each match date
                              |           (point-in-time: results strictly before kickoff)
                              v
  MART       mart_match_features ......... one row per match, model-ready
                              |
                    +---------+---------+
                    |                   |
                    v                   v
              Model A: baseline    Model B: prorel
              calendar, lags,      everything in A
              match context        + rank_before, rank_gap,
                    |              points_from_playoff_line, ...
                    |                   |
                    +---------+---------+
                              v
              predictions, model_metrics, feature_importance
                              |
                              v
                   [ Tableau ]  three views + tracker strip
                   DuckDB JDBC connector, or CSV/Hyper extracts
```

Everything runs on one machine against a single DuckDB file. Phase one is scheduled
by a plain weekly task on Tuesdays. Dagster and weather features are
[phase two](#phase-two-deferred).

With `FOOTYSTATS_API_KEY` unset, the whole pipeline runs from `data/raw_archive/`.
That is the intended state after the subscription month ends, and it is how anyone
cloning this repo runs it without paying for anything.

### The stack

Two pieces of infrastructure - a DuckDB file on disk and Tableau - and Python
between them. Nothing listens on a port, nothing needs an API key, nothing
authenticates.

| | |
|---|---|
| Ingest | FootyStats JSON API via `requests`; key from `.env` via `python-dotenv` |
| Archive | Raw responses to `data/raw_archive/`, committed. Written before parsing |
| Store | DuckDB. One file, single writer |
| Transform | SQL, executed inside DuckDB. Python reads the `.sql` file and hands it over |
| Model | `xgboost`, with `scikit-learn` for the error metrics |
| Export | `pandas.to_csv`, or Hyper via the optional `pantab` |
| Visualise | Tableau - live via the DuckDB JDBC connector, or on the CSV extracts |
| Schedule | Windows Task Scheduler. Outside Python, and the one place a failure will not produce a traceback |

`xgboost` is the only heavy install; it ships a compiled wheel. Dagster, the
weather API, and everything cloud-shaped are out of scope for phase one.

The FootyStats subscription is the one paid dependency and the one secret. It runs
for a single month, which makes data acquisition the project's first hard deadline -
see [phase 00](docs/phases/00-data-access-and-the-clock.md).

---

## Two tracks through this repo

The documentation ships in two parallel tracks. Same architecture, different depth.

| | [MVP track](docs/mvp/) | [Full track](docs/phases/) |
|---|---|---|
| Goal | Something running end to end, fast | The portfolio-grade build |
| Ingest | One season, free `example` key | Nine seasons, backfill + weekly delta |
| Idempotency | Primary key + upsert | Same, plus inserted/updated/unchanged logging |
| SQL | Two tiers collapsed into one file | Three genuinely separate tiers |
| Standings | League-wide rank | Conference rank, point-in-time, tie-broken |
| Models | Both models, default hyperparameters | Both models, chronological CV, naive baseline |
| Tableau | CSV extracts into Tableau Public | DuckDB JDBC connector during the Desktop trial |
| Schedule | Windows Task Scheduler, one command | Same, plus full run-metadata logging |
| Time | An afternoon | The build order at the bottom of this file |

Start with the MVP track if you want the shape of the thing in your hands today.
Start with the full track if you are building the version you will show someone.
The MVP track is a strict subset - nothing in it has to be thrown away.

---

## Setup

Requires Python 3.11 or newer.

```
git clone <this repo>
cd USL-ticketing-and-stadium-revenue-forecasting

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements-dev.txt
pip install -e .
```

Working through the [MVP track](docs/mvp/) instead? `pip install -r requirements-mvp.txt`
installs only the seven packages that track uses.

Verify the install:

```
make lint          # passes on a fresh clone
make typecheck     # passes on a fresh clone
make test          # partly red on a fresh clone, by design
```

On a fresh clone, lint and typecheck are green and the test suite is not: it collects
51 tests, of which some pass (the ones checking the feature definitions and the SQL
layer's wiring, which need no implementation), some fail with `NotImplementedError`,
and the rest skip with a TODO naming what to build.

That is the point - the stubs under `usl/` are yours to implement, and the tests
describe what correct looks like. `make check` runs all three and therefore fails
until you have implemented enough to turn it green. See
[tests/README.md](tests/README.md).

---

## How to run

Each stage is a subcommand of one CLI. `make` targets wrap them; the underlying
command is shown so Windows users without `make` can run them directly.

| Task | Make | Direct |
|---|---|---|
| Backfill every configured season | `make backfill` | `python -m usl.run backfill` |
| Ingest the current season only | `make ingest` | `python -m usl.run ingest` |
| Report what the archive holds | `make archive` | `python -m usl.run archive` |
| Run the SQL layer | `make transform` | `python -m usl.run transform` |
| Train both models | `make train` | `python -m usl.run train` |
| Write Tableau extracts | `make export` | `python -m usl.run export` |
| The full Tuesday run | `make weekly` | `python -m usl.run weekly` |

First time through, in order:

```
make backfill      # one-time, a few thousand rows, be polite to the source
make transform
make train
make export
```

After that, `make weekly` is the only command you need. Schedule it for Tuesday
morning - the weekend fixtures are posted and settled by then. Setup instructions
for Windows Task Scheduler are in [docs/mvp/05-mvp-schedule.md](docs/mvp/05-mvp-schedule.md).

---

## Repository layout

```
.
+-- docs/                 The build guide. Start at docs/README.md
|   +-- mvp/              Minimum-viable track
|   +-- phases/           Full track, one doc per phase
|   +-- reference/        Cross-cutting notes: logging, Tableau setup, open questions
+-- usl/                  The package
|   +-- config.py         Season range, paths, feature lists, tunables
|   +-- logging_setup.py  Structured run logging. First-class, not an afterthought
|   +-- db.py             DuckDB connection and write strategy
|   +-- run.py            CLI entry point
|   +-- ingest/           footystats.py (API client), archive.py (durable raw store)
|   +-- load/             raw.py - upsert into raw_matches
|   +-- sql/              The three-tier SQL layer, one .sql file per model
|   +-- transform/        SQL runner and data-quality checks
|   +-- features/         Feature list definitions shared by both models
|   +-- models/           train.py, metrics.py
|   +-- export/           Tableau extract writer
|   +-- weather/          Phase two stubs, Open-Meteo
|   +-- ref/              Hand-maintained CSVs. Treat as code, not data
+-- data/                 usl.duckdb (gitignored, rebuildable)
|   +-- raw_archive/      Every raw API response. COMMITTED. Not regenerable
+-- demo/                 Break-and-fix scenarios and saved HTML fixtures
+-- tableau/              Workbook and extract output
+-- tests/                Test stubs for the transformations with clear correctness criteria
+-- scripts/              Scheduler entry points, and the attendance gate check
```

Two hand-maintained reference files under `usl/ref/` are load-bearing:
`club_aliases.csv` maps every raw club string ever seen to a canonical `club_id`, and
`stadiums.csv` carries coordinates with validity ranges. Both are small, both are
checked into git, and both are code.

---

## What is validated, and what is not

Stated up front because it is the most likely question in an interview, and burying
it is worse than leading with it.

**Measured.** Calendar, lag, and match-context features. The dead-rubber decay curve -
real attendance on eliminated-club home matches across all nine seasons. These are
findings.

**Measured, but a partial proxy.** `points_from_playoff_line`, `is_mathematically_live`,
`rank_before`. These show table position affects attendance. They do not size the
relegation effect, because a playoff race measures upside stakes and relegation
measures downside, existential stakes.

**Instrumented, unvalidated.** `points_from_relegation_line`. No relegation exists in
the data, so it has no ground truth. It is built, its importance is logged, and it is
labelled in the dashboard and here as a forward-looking instrument rather than a
predictor.

2020 attendance is not demand signal. `is_covid_affected` flags it and `DROP_COVID`
switches it out, defaulting to on for training.

---

## Phase two, deferred

Documented, scoped, and not built. Both are clearly labelled future work rather than
gaps.

- **[Dagster orchestration](docs/phases/11-phase-two-dagster.md)** - asset lineage, run
  history, asset checks, and materialization metadata plotted over time. Phase one uses
  a plain scheduled task, which does the job but leaves no run history to browse.
- **[Weather features via Open-Meteo](docs/phases/12-phase-two-weather.md)** - free, no
  API key, historical archive plus forecast. Needs `stadiums.csv` with validity ranges
  so a club that moved grounds does not corrupt its older matches.

---

## Build order

**There are two clocks, and the expensive one runs first.**

| Clock | Length | Lapsing costs you |
|---|---|---|
| FootyStats subscription | ~30 days | The data, permanently, unless archived |
| Tableau Desktop trial | 14 days | Only the live connection; extracts still work |

Do not start them in the same month if you can avoid it. Pull and archive the data,
let the subscription lapse, then start Tableau against the archive.

**Before you subscribe** - free, unlimited, no clock running:

0. **`python scripts/check_attendance_coverage.py`** - free, one minute. Confirms an
   attendance field exists at all before you commit to this provider.
1. Build the whole ingest client against the FootyStats `example` key (EPL 2018/19).
   Auth, retry, archiving, parsing, the schema guard, the loader.
2. Get `stg_matches` and the idempotency guard working on that one example season.

**During the subscription month** - the clock is running, so pull broadly:

3. `league-list`, find USL Championship, write every season id into `usl/ref/seasons.csv`.
4. **Run the attendance gate against a real USL season** -
   `python scripts/check_attendance_coverage.py --season-id <id>`. There is no
   fallback source, so this is the check the project rests on.
5. Backfill every season, plus league tables as a standings cross-check. Archive it all.
6. Verify the pipeline runs end to end with the key removed. Then let it lapse.

**After it lapses** - free again, everything served from the archive:

7. Staging plus club aliases, failing loudly on unmapped names.
8. `int_standings`. The hardest SQL. Do it before the fun parts.
9. Mart plus features.
10. Both models, all three output tables.
11. Tableau extracts, so the repo is useful to someone with no Tableau at all.
12. Schedule the weekly task. Note it can only refresh live data while subscribed.
13. **Then** start the Tableau Desktop trial and build the live dashboard.
14. Record, write the delivery email, send.

Weather and Dagster slot in after step 14, as phase two.

---

## License

MIT. See [LICENSE](LICENSE).
