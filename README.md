# USL Attendance Forecasting

A working demand model on match attendance, and a measurement framework for the
promotion-and-relegation effect that arrives in USL in 2028.

**Headline question: does league position predict attendance?**

The project answers it by training two models on one dataset. Model A is blind to
the table. Model B sees conference rank and the stakes features derived from it.
They share every upstream table and split only at the model layer, so any
difference in error is attributable to those features and nothing else.

---

## Where this stands

The build guide under `docs/` has been worked through from phase 00 to phase 09.
Every stub under `usl/` is implemented, every test is real, `make check` is green on
a fresh clone, and the whole pipeline - archive, load, six SQL models, nine checks,
both models plus the naive baseline, extracts, the weekly command, seven demo
scripts - runs end to end from the committed archive with no API key.

It runs today on the one season the free FootyStats `example` key serves: **English
Premier League 2018/19**, 380 matches, attendance on every one. That is exactly the
sequencing the guide prescribes - build and prove the whole thing before paying for
a single request - and it means the numbers below are EPL numbers, not USL numbers.
Pointing it at nine seasons of USL Championship is the subscription month's work,
and it is listed step by step under [what is left to do by hand](#what-is-left-to-do-by-hand).

Every choice the guide left open is written down in
[docs/reference/build-decisions.md](docs/reference/build-decisions.md).

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

> Here is a working demand model, and here is a measurement framework for the
> pro-rel effect, instrumented now, so that when relegation arrives there is a
> baseline to compare against.

**What this can prove:** a model that predicts match attendance and stays accurate
week over week.

**What this cannot prove:** that relegation pressure moves attendance in USL. It has
not happened yet. Saying so plainly is a stronger move than pretending otherwise. See
[the honesty note](docs/phases/06-features.md#the-honesty-note) for exactly where the
line falls between measured, proxied, and instrumented-but-unvalidated.

---

## Architecture

```
                    FootyStats API (paid, 1 month; free 'example' key for EPL 2018/19)
                              |
                      [ usl/ingest/ ]  archive every response BEFORE parsing
                              |
                              v
            data/raw_archive/ ........... committed to git. The only copy of the
                              |           source data once the subscription lapses
                              v
  RAW        raw_matches ................. exactly as returned, never edited. Upserted
                              |           with an inserted / updated / unchanged split
                              |  usl/ref/*.csv  (six hand-maintained files, load-bearing)
                              v
  STAGING    stg_clubs, stg_matches ...... types, canonical club_id, one row per fixture
                              |           checks: fresh, mapped, row count, unique, one
                              |           match per club per date, conference known
                              v
  INTERMEDIATE  int_standings ............ full-field conference rank as of each date
                int_stakes ............... playoff and relegation lines, mathematically
                              |           live, eliminated_on.  check: no future leakage
                              v
  MART       mart_match_features ......... one row per fixture, model-ready
             mart_decay_curve ............ eliminated-club gates indexed to own baseline
                              |           checks: features not null, mart matches staging
                    +---------+---------+
                    |                   |
                    v                   v
              Model A: baseline    Model B: prorel
              calendar, lags,      everything in A
              match context        + rank_before, rank_gap,
                    |              points_from_playoff_line, ...
                    +---------+---------+
                              v
              predictions, model_metrics, feature_importance,
              model_variance (seeds), model_cv (expanding window by season)
                              |
                              v
                   [ Tableau ]  three views + tracker strip, from tableau/extracts/
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
| Store | DuckDB. One file, single writer, lock retried then reported by name |
| Transform | SQL, executed inside DuckDB. Python reads the `.sql` file and hands it over |
| Model | `xgboost`, with `scikit-learn` for the error metrics |
| Export | `pandas.to_csv`, or Hyper via the optional `pantab` |
| Visualise | Tableau - live via the DuckDB JDBC connector, or on the CSV extracts |
| Schedule | Windows Task Scheduler or cron. Outside Python, and the one place a failure will not produce a traceback |

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
| Standings | League-wide rank | Conference rank, point-in-time, tie-broken, full field |
| Models | Both models, default hyperparameters | Both models, chronological holdout, seed variance, expanding-window CV, naive baseline |
| Tableau | CSV extracts into Tableau Public | DuckDB JDBC connector during the Desktop trial |
| Schedule | Windows Task Scheduler, one command | Same, plus full run-metadata logging |
| Time | An afternoon | The build order at the bottom of this file |

The code in `usl/` is the full track. The MVP track's first pass is preserved as a
working one-file experiment under `usl/experiments/MVP 1/`, with its findings.

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
cp .env.example .env          # leave FOOTYSTATS_API_KEY empty; the archive serves everything
```

Verify the install:

```
make check         # lint + typecheck + test. Green on a fresh clone
```

Then run the pipeline from the archive:

```
make backfill      # 380 EPL matches from data/raw_archive/, no key needed
make transform     # six SQL models, nine checks
make train         # both models, the naive baseline, seed variance, CV
make export        # CSVs into tableau/extracts/
```

`make backfill` a second time reports zero inserted and 380 unchanged. That is the
idempotency guard, and it is the first thing anyone tries.

---

## How to run

Each stage is a subcommand of one CLI. `make` targets wrap them; the underlying
command is shown so Windows users without `make` can run them directly.

| Task | Make | Direct |
|---|---|---|
| Backfill every season in `usl/ref/seasons.csv` that has an id | `make backfill` | `python -m usl.run backfill` |
| Ingest the current season only | `make ingest` | `python -m usl.run ingest` |
| Report what the archive holds | `make archive` | `python -m usl.run archive` |
| List leagues and season ids (needs a key) | `make league-list` | `python -m usl.run league-list` |
| Run the SQL layer | `make transform` | `python -m usl.run transform` |
| Train both models | `make train` | `python -m usl.run train` |
| Write Tableau extracts | `make export` | `python -m usl.run export` |
| The full Tuesday run | `make weekly` | `python -m usl.run weekly` |
| The break-and-fix menu | `make demo-list` | see `demo/README.md` |

Every command writes one row per stage to `run_log` and one per check to
`check_log`, in the database, so "when did this last update" is answerable from
inside Tableau. Exit codes: 0 green, 1 a stage or a check failed, 3 the database was
locked for the whole retry window.

After the first pass, `make weekly` is the only command you need. Schedule it for
Tuesday morning - the weekend fixtures are posted and settled by then. Setup
instructions are in [docs/mvp/05-mvp-schedule.md](docs/mvp/05-mvp-schedule.md).

---

## Results on the example season

<!-- RESULTS_TABLE -->

---

## Repository layout

```
.
+-- docs/                 The build guide. Start at docs/README.md
|   +-- mvp/              Minimum-viable track
|   +-- phases/           Full track, one doc per phase
|   +-- reference/        Logging, Tableau setup, open questions, and build decisions
+-- usl/                  The package
|   +-- config.py         Season range, paths, feature lists, tunables. Judgement calls marked
|   +-- logging_setup.py  Structured run logging. First-class, not an afterthought
|   +-- db.py             DuckDB connection and the lock guard
|   +-- run.py            CLI entry point
|   +-- ingest/           footystats.py (API client), archive.py (durable raw store)
|   +-- load/             raw.py - upsert into raw_matches
|   +-- sql/              The SQL layer, six .sql files, one per model
|   +-- transform/        SQL runner, nine data-quality checks, reference-table loader
|   +-- features/         Feature list definitions shared by both models
|   +-- models/           train.py, metrics.py
|   +-- export/           Tableau extract writer
|   +-- weather/          Phase two stubs, Open-Meteo
|   +-- ref/              Six hand-maintained CSVs. Treat as code, not data
|   +-- experiments/      The first MVP pass, kept as a working one-file experiment
+-- data/                 usl.duckdb (gitignored, rebuildable)
|   +-- raw_archive/      Every raw API response. COMMITTED. Not regenerable
+-- demo/                 Four break-and-fix scenarios, three working-behaviour demos, fixtures
+-- tableau/              Extract output and the view-by-view spec
+-- tests/                The suite. Green on a fresh clone
+-- scripts/              Scheduler entry points, and the attendance gate check
```

Two hand-maintained reference files under `usl/ref/` are load-bearing above the
rest: `club_aliases.csv` maps every raw club string ever seen to a canonical
`club_id`, and `club_conference.csv` carries conference and display name per
club-season. Both are small, both are checked into git, and both are code.

---

## What is validated, and what is not

Stated up front because it is the most likely question in an interview, and burying
it is worse than leading with it.

**Measured.** Calendar, lag, and match-context features. The dead-rubber decay curve -
real attendance on eliminated-club home matches, indexed to each club's own
pre-elimination baseline. These are findings.

**Measured, but a partial proxy.** `points_from_playoff_line`, `is_mathematically_live`,
`rank_before`. These show table position affects attendance. They do not size the
relegation effect, because a playoff race measures upside stakes and relegation
measures downside, existential stakes.

**Instrumented, unvalidated.** `points_from_relegation_line`. No relegation exists in
USL data, so it has no ground truth there. It is built, its importance is logged, and
it is labelled in the dashboard and here as a forward-looking instrument rather than a
predictor. One caveat in the other direction: the example season is the EPL, where
relegation is real, so on today's data the instrument does have ground truth. That is
a sanity check on the feature, not evidence about USL.

2020 attendance is not demand signal. `is_covid_affected` flags it and `DROP_COVID`
switches it out, defaulting to on for training. The window - 1 March 2020 to 30 June
2021 - is a range chosen in `config.py`, not a fact.

The match date is taken in UTC. That is exact for England and a judgement call for
the US; `config.MATCH_TZ` is where to change it before USL data arrives, and
[build-decisions.md](docs/reference/build-decisions.md#phase-04---standings) says why
it matters for west-coast Saturday nights.

---

## What is left to do by hand

Three things need something this environment did not have: a paid key, a Tableau
licence, and a machine that stays on.

**The subscription month** - free steps first, then the clock:

1. `python scripts/check_attendance_coverage.py` passes today from the archive. On
   day one of the subscription run it again with `--season-id <a USL season id>`.
   That is the gate; nothing else matters if it fails.
2. Put the key in `.env`. Run `python -m usl.run league-list`, find USL Championship,
   and paste each season's id into `usl/ref/seasons.csv` (the rows are there with
   blank ids). **Delete the EPL row and run `make clean-db`** so one database holds
   one league.
3. Fill `usl/ref/club_aliases.csv` and `usl/ref/club_conference.csv` for the USL
   clubs. The transform tells you exactly which strings are unmapped, and which
   club-seasons have no conference, so this is a loop of running `make transform`
   and pasting. Add a row per season and conference to
   `usl/ref/conference_structure.csv` with that season's playoff spots. Set
   `config.MATCH_TZ` to a US zone.
4. `make backfill`. Also pull `league-tables` per season while you can (the client
   has `fetch_league_table`); it is the published-table cross-check for the
   standings, which on the EPL season is done by the test suite instead.
5. `git add data/raw_archive && git commit`. Then unset the key and run
   `make weekly`. If it is green, the subscription can lapse.

**Tableau.** The extracts and the view-by-view spec are in
[tableau/README.md](tableau/README.md). Build the three views and the tracker
strip in Tableau Public against `tableau/extracts/*.csv`; start the 14-day Desktop
trial only for the live connection and the video.

**The scheduler.** Register `scripts/run_weekly.ps1` in Task Scheduler (or the
`.sh` in cron) per [docs/mvp/05-mvp-schedule.md](docs/mvp/05-mvp-schedule.md), and
set `config.CURRENT_SEASON` so the freshness check has something to be fresh about.

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

**Before you subscribe** - free, unlimited, no clock running. **Done.**

0. `python scripts/check_attendance_coverage.py` - confirms an attendance field exists.
1. Build the whole ingest client against the FootyStats `example` key (EPL 2018/19).
2. Get `stg_matches` and the idempotency guard working on that one example season.

**During the subscription month** - the clock is running, so pull broadly. **Yours.**

3. `league-list`, find USL Championship, write every season id into `usl/ref/seasons.csv`.
4. Run the attendance gate against a real USL season. There is no fallback source.
5. Backfill every season, plus league tables as a standings cross-check. Archive it all.
6. Verify the pipeline runs end to end with the key removed. Then let it lapse.

**After it lapses** - free again, everything served from the archive. **Built and
proven on the example season; re-run on USL.**

7. Staging plus club aliases, failing loudly on unmapped names.
8. `int_standings`. The hardest SQL. Verified against the published EPL table.
9. Mart plus features, and the decay curve.
10. Both models, the naive baseline, all five output tables.
11. Tableau extracts, so the repo is useful to someone with no Tableau at all.
12. Schedule the weekly task.
13. **Then** start the Tableau Desktop trial and build the live dashboard. **Yours.**
14. Record, write the delivery email, send. **Yours.**

Weather and Dagster slot in after step 14, as phase two.

---

## License

MIT. See [LICENSE](LICENSE).
