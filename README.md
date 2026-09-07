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
a fresh clone, and the whole pipeline - archive, load, seven SQL models, seventeen checks,
both models plus the naive baseline, extracts, the weekly command, seven demo
scripts - runs end to end from the committed archive with no API key. Phase two
is built as well: the same pipeline as a Dagster asset graph with the checks as
blocking asset checks, and match-day weather from Open-Meteo as a shared feature
family, with the example season's observed weather archived beside the match data
so that run needs no network either (see [phase two](#phase-two-built)).

It was built and proven on the one season the free FootyStats `example` key serves,
English Premier League 2018/19, exactly as the guide prescribes: prove the whole
thing before paying for a single request. It now runs on **ten seasons of USL
Championship, 2017 to 2026**, 4,578 matches pulled during the subscription month
and committed under `data/raw_archive/`, so the USL run needs no key either. The
numbers below are USL numbers. The example season is still one command away
(`USL_SEASONS_CSV=usl/ref/seasons.example.csv`), and the tests and demos run on it.

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
[phase two](#phase-two-built).

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
| Ingest | One season, free `example` key | Ten seasons, backfill + weekly delta |
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

No `make` on your machine? Windows does not ship it. `python make.py <target>`
reads the Makefile and runs the same lines, so every `make` command in this
README works as `python make.py check`, `python make.py backfill`, and so on.
Or run the underlying commands directly: each target is one `python -m` line,
listed by `python make.py help`.

Then run the pipeline from the archive:

```
make backfill      # ten USL Championship seasons from data/raw_archive/, no key needed
make transform     # seven SQL models, seventeen checks
make train         # both models, the naive baseline, seed variance, CV
make export        # CSVs into tableau/extracts/
```

`make backfill` a second time reports zero inserted and everything unchanged. That
is the idempotency guard, and it is the first thing anyone tries.

To run the EPL example season the pipeline was built against instead, point the
backfill at its own seasons file; the tests and demos do the same:

```
USL_SEASONS_CSV=usl/ref/seasons.example.csv make backfill
```

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

## Results on the USL Championship, 2017 to 2026

<!-- RESULTS_START -->
Produced by `make backfill && make transform && make train && make export` on the ten archived USL Championship seasons, 2017 to 2026 to date (run date 2026-09-07). Playoff matches are in the mart and out of the table; COVID-window rows are dropped before training.

| Table | Rows |
|---|---|
| `raw_matches` | 4,578 |
| `stg_matches` | 4,578 |
| `stg_weather` | 92,853 |
| `int_standings` | 23,640 |
| `int_stakes` | 23,640 |
| `mart_match_features` | 4,569 |
| `mart_decay_curve` | 6 |

Every check passed on the latest run:

| Check | Tier | Passed |
|---|---|---|
| `no_future_leakage` | intermediate | yes |
| `features_not_null` | mart | yes |
| `mart_matches_staging` | mart | yes |
| `played_weather_is_observed` | mart | yes |
| `all_club_seasons_have_conference` | staging | yes |
| `all_clubs_mapped` | staging | yes |
| `all_conference_clubs_have_fixtures` | staging | yes |
| `conference_membership_is_plausible` | staging | yes |
| `conference_structure_is_well_formed` | staging | yes |
| `derby_clubs_are_known` | staging | yes |
| `home_matches_resolve_to_one_stadium` | staging | yes |
| `matches_are_fresh` | staging | yes |
| `one_conference_per_club_season` | staging | yes |
| `one_match_per_club_per_date` | staging | yes |
| `one_row_per_match` | staging | yes |
| `played_rows_consistent` | staging | yes |
| `row_count_preserved` | staging | yes |

Holdout error (chronological split, last 20 percent of played matches):

| Model | MAE (attendees) | MAPE | RMSE | Train | Test |
|---|---|---|---|---|---|
| `naive_club_mean` | 1,593 | 351.7% | 2,330 | 1881 | 470 |
| `baseline` | 1,046 | 227.3% | 1,759 | 1881 | 470 |
| `prorel` | 1,040 | 215.6% | 1,755 | 1881 | 470 |

Run-to-run noise across seeds (`model_variance`), the floor the A-to-B gap has to clear:

| Model | Min MAE | Max MAE | Seeds |
|---|---|---|---|
| `baseline` | 1,040 | 1,056 | 4 |
| `prorel` | 1,016 | 1,040 | 4 |

Top five features by gain, `baseline`: `home_gate_ma5` 98,167,608, `home_gate_ma3` 50,413,656, `last_home_gate` 8,806,992, `is_final_home_match` 4,613,592, `opponent_club_id` 3,446,796

Top five features by gain, `prorel`: `home_gate_ma5` 134,818,320, `home_gate_ma3` 36,892,128, `last_home_gate` 13,802,647, `is_season_opener` 5,445,246, `is_final_home_match` 4,279,539

Features the pro-rel model never split on (logged as zero, not absent): `is_derby`

The dead-rubber decay curve (`mart_decay_curve`), attendance on eliminated-club home matches indexed to each club-season's own pre-elimination mean. Elimination here means mathematically out of the playoff race - eight of twelve per conference in recent seasons, which is why so few matches ever qualify:

| Home matches since elimination | n | Index vs own baseline | Club-seasons |
|---|---|---|---|
| 0 | 32 | 1.11 | 32 |
| 1 | 22 | 1.06 | 22 |
| 2 | 10 | 1.21 | 10 |
| 3 | 2 | 1.03 | 2 |
| 4 | 2 | 0.71 | 2 |
| 5 | 1 | 1.20 | 1 |

### Reading the result honestly

This is exercise 7.2 on ten seasons of the league the project is about, 2017 to
the 2026 season in progress, with observed match-day weather on every played row
and a forecast on the coming ones.

- **Both models beat the naive baseline, by a lot.** The club's mean home gate is
  about 550 attendees worse than either XGBoost model on the holdout, and worse
  on every one of the five expanding-window folds, by 250 to 660. With 1,881
  training rows the lag features stop being the club mean with noise added and
  start carrying the calendar, the opponent and the run of form. That is the
  demand model working.
- **The A-to-B gap is noise.** Model B beats Model A by six attendees on the
  holdout, inside seed spreads of 16 and 24; on the five folds Model A wins every
  time, by 5 to 52. The pro-rel features are used - `rank_gap` and
  `points_from_relegation_line` are ninth and tenth by gain in Model B - and there
  is no finding on the headline question with no relegation in the data. Saying
  so is the point.
- **MAPE is broken by one row, and MAE is not.** New Mexico United v Orange
  County SC on 13 June 2026 carries a gate of 8 against a club mean above 9,000;
  both models predicted about 7,000, and that single row lifts the holdout MAPE
  from 28 percent to over 200. Ten seasons hold four gates under 100 (50, 18, 2
  and 8), all provider errors. They stay in until a minimum plausible gate is a
  written-down decision, and MAE, which one row cannot move by more than 15
  attendees, is the headline number.
- **Weather is real and small.** Precipitation is eleventh by gain in both
  models, and no weather column reaches either top ten. Rain on a match day
  matters less than who is playing and what the club drew last time, which is
  what you would expect of a league whose fixtures are mostly in summer.
- **There are almost no dead rubbers.** Only 69 of 2,300 gated regular-season
  home matches were played after the club was mathematically out of the playoff
  race, because eight of twelve qualify and the arithmetic keeps a club alive
  until the last fortnight. The curve above is drawn on those 69 and shows
  nothing. This is the thesis in one number: a closed league with a low playoff
  line has no "nothing at stake" condition to measure. Pro-rel creates one.
- **The attendance record has a hole.** The provider carries a gate on 93 to 100
  percent of 2017 to 2019 matches, on 12 percent of 2020, on none of 2021 to
  2023, on 57 to 61 percent of 2024 and 2025, and on 85 percent of 2026 so far,
  missing by month rather than by club. 2,386 labelled matches in all. The lag
  history restarts after a gap longer than 400 days, so no 2024 opener inherits
  a 2019 crowd; the price is that 2024's first home matches have null lags, which
  the model handles. `is_derby` was never split on because no USL derby pairs
  are listed yet.
- **The standings are exact.** Every one of the 289 club-season totals matches
  the provider's published table once playoff points are added, and the
  regular-season group tables agree club for club for the 181 club-seasons the
  provider publishes them for. `scripts/verify_standings.py` checks it.
- **The season in progress is live.** 281 matches played, 94 to play, and a
  forecast on all 94 for both models. The 27 fixtures inside the 16-day horizon
  carry forecast weather from the 7 September snapshot, the 67 beyond it none
  yet, and the 12 matches played in the last week wait for the observation
  archive to catch up. Each weekly run moves all three counts.

What the run does prove: the pipeline lands ten seasons, reconstructs standings
that match the published tables exactly, keeps playoff matches out of the table
and in the model, joins observed weather to every one of the 4,463 played home
fixtures older than a week from an archive that needs no network, builds every
feature without leakage, trains both models on identical rows, forecasts the
fixtures still to come, and records enough per-run history that the comparison
can be read against noise instead of against a single point estimate.
<!-- RESULTS_END -->

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
|   +-- transform/        SQL runner, seventeen data-quality checks, reference-table loader
|   +-- features/         Feature list definitions shared by both models
|   +-- models/           train.py, metrics.py
|   +-- export/           Tableau extract writer
|   +-- weather/          Phase two: Open-Meteo client and refresh, archive-first
|   +-- assets/           Phase two: the Dagster assets and asset checks
|   +-- defs.py           Phase two: Dagster definitions, weekly job and schedule
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
predictor. It did have ground truth once: the EPL example season the pipeline was
built on has real relegation, which served as a sanity check on the feature before
the USL data arrived. On USL data it is an instrument waiting for 2028.

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

**The subscription month, what is done and what is left.** The ten USL seasons,
2017 to 2026 to date, are pulled, archived and committed; every club id is
mapped; the conference lists are verified against the provider's tables; the
standings match the published ones club for club, 2026 to date included; the
weather for every USL ground is archived to the last week of August 2026, with
the forecasts for the coming fortnight beside it; the transform, the training
and the export run on all of it with no key. Left, in order of value:

1. **Turn on the weekly live run.** The season in progress is loaded with its
   weather: 25 clubs, 281 played, 94 to play, a forecast on every one of them.
   What is left is a machine that runs on Tuesdays with `USL_CURRENT_SEASON=2026`
   in `.env`: the weekly ingest then re-pulls the season as a dated snapshot,
   the weather stage tops up last week's observations and refreshes the
   forecasts, and the freshness check applies. Either scheduler below does it.
2. **A second attendance source for 2021 to 2023.** The provider carries no gate
   for those seasons. The pipeline trains on what has one, and the hole is
   documented in the results; a second source joined on season, date and club
   would fill it. FBref's fixture pages are the first place to look.
3. **A USL derby list.** `usl/ref/derbies.csv` has only the EPL pairs, so
   `is_derby` is false on every USL row. Same rule as the file's note: shared
   metro, or marketed as a derby by both clubs.

**Tableau.** The extracts and the view-by-view spec are in
[tableau/README.md](tableau/README.md). Build the three views and the tracker
strip in Tableau Public against `tableau/extracts/*.csv`; start the 14-day Desktop
trial only for the live connection and the video.

**The scheduler.** Either register `scripts/run_weekly.ps1` in Task Scheduler (or
the `.sh` in cron) per [docs/mvp/05-mvp-schedule.md](docs/mvp/05-mvp-schedule.md),
or run `make dagster` and turn on the `weekly_tuesday` schedule - one or the
other, never both, since they write the same file. For Dagster, set `DAGSTER_HOME`
to a directory that stays, or the run history is gone when the UI closes, and keep
`dagster dev` running: the schedule fires from it. Set `USL_CURRENT_SEASON=2026`
in `.env` so the freshness check has something to be fresh about.

---

## Phase two, built

Both pieces the guide deferred are in, and both keep the phase-one commands as
the source of truth.

- **[Dagster orchestration](docs/phases/11-phase-two-dagster.md).** `usl/defs.py`
  and `usl/assets/` wrap the same functions `python -m usl.run` calls: one asset per
  raw table, reference CSV and SQL model, a multi-asset for the five model tables,
  one for the extracts, and every data-quality check as a blocking asset check on
  its tier. Assets run serially in one process, because DuckDB is single-writer,
  and each writes its stage to the run log under the Dagster run id, so the
  Tableau tracker keeps working. `make install-dagster` then `make dagster` opens
  the UI with the `weekly_tuesday` schedule defined; `tests/test_dagster.py`
  materialises the whole graph on the example season.
- **[Weather via Open-Meteo](docs/phases/12-phase-two-weather.md).** `usl/weather/`
  fetches observed daily weather for played home matches and forecasts for the
  coming ones, one request per club and ground, archived beside the FootyStats
  responses and never requested twice. `usl/ref/stadiums.csv` has coordinates for
  every EPL and USL club with validity ranges where a club moved. Five weather
  features join both models; `weather_source` and the forecast horizon ride along
  so a forecast is never mistaken for an observation, and a check fails the run
  if a played match keeps forecast weather. The example season's observed
  weather is archived (21 responses, 5,316 club-days), so the archive-only run
  joins real weather to all 380 matches without a network; `USL_WEATHER_ENABLED=0`
  turns the stage off. `make weather` fetches only what is still missing.

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
