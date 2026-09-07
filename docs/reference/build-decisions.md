# Build decisions

The guide leaves choices open on purpose and says, each time, "write it down".
This is where they are written down. Every decision below names the phase or the
open question it answers, what was chosen, and why. Where the choice was made
because of the data actually available at build time - the free example season
rather than ten seasons of USL - that is said too.

The companion list of questions is [open-questions.md](open-questions.md); this
page is the answers.

---

## The data at build time

The pipeline was built and verified against the one season the free `example` key
serves: English Premier League 2018/19, FootyStats season id `1625`, 380 matches,
attendance populated on all 380. That is the sequencing phase 00 prescribes - the
whole client, SQL layer, both models, and the export were finished before a single
paid request.

Consequences that follow from that and are visible in the repo:

- `data/raw_archive/league-matches_season_id_1625.json` is the archived response,
  byte for byte, committed.
- `usl/ref/*.csv` carry the twenty EPL clubs. The USL rows are the work of the
  subscription month and are marked `TODO` in `seasons.csv`.
- The single "conference" for the example season is the whole league. Ranking
  within conference and ranking league-wide are the same thing when there is one
  conference, so the conference machinery is exercised without being tested at its
  hardest. `tests/test_standings.py` covers the two-conference case on a fixture.
- The EPL has real relegation. That makes the example season the one place where
  `points_from_relegation_line` has ground truth, and it is treated as a sanity
  check on the instrument, not as evidence about USL.

---

## Phase 00 - archive naming and the key

`archive_path(endpoint, params)` produces `<endpoint>_<k1>_<v1>_<k2>_<v2>.json`
with the params sorted by name: `league-matches_season_id_1625.json`. Readable,
browsable, and the cache key is obvious on sight. The `key` parameter is stripped
before the filename is built and never appears inside the file either, because the
API returns the request metadata without it.

The key never reaches a log line by construction (the client logs endpoint,
status, and byte count, never a URL) and by a `RedactSecretsFilter` on every log
handler as a second guard. `tests/test_footystats.py` asserts both. The filter
leaves the public `example` key alone, so the archive-only logs stay readable.

**Archive-before-parse, made safe to force.** The guide's rule is that a body is
written before it is parsed. Written where matters: a fresh body lands in
`<slot>.partial`, is parsed, and only a body that is JSON and does not say
`"success": false` replaces the archived copy, in one `os.replace`. A body that
fails either check is moved to `<slot>.bad` - kept for inspection, never served
as a hit - and whatever was archived before is untouched. So `--force` against a
lapsed key cannot overwrite the only copy of a season with an error envelope, and a
crash mid-write leaves a `.partial` that is never read rather than a half file that
is. A zero-byte file is not a hit either. `.partial` and `.bad` are gitignored;
the `archive` command counts quarantined files so they get looked at.

**Dated snapshots for a live season.** A completed season is one archive entry and
is never re-requested. A season still being played has to be re-requested every
week, and `--force` is the wrong tool for that because it overwrites. So the weekly
ingest passes the run date as a `snapshot`, which archives the pull as
`league-matches_season_id_<id>_as_of_<date>.json` beside the others - one entry per
pull, nothing overwritten, and the date is not sent to the API. Without a key the
newest snapshot is served, or the undated backfill copy if there is none, with a
warning that says nothing is being refreshed. `scripts/check_attendance_coverage.py`
goes through the same client, so a coverage survey during the paid month is
archived too rather than spent.

## Phase 01 - the raw table and `match_id`

**Raw shape.** `raw_matches` lifts fourteen scalar fields into columns, renamed to
snake_case but stored as the API sent them - goals and attendance as text, the
season as `"2018/2019"`, kick-off as unix seconds - and carries the complete match
record in `raw_json`. Renaming is naming, not typing. Nothing is discarded.

**`match_id`.** `"fs:" + id` when the frame carries the provider's `id`. When it
does not - a frame from a second source, or the hand-built test fixtures - the
fallback is `"nk:" + sha1(season|date|home_raw|away_raw)[:16]`, which is the
natural key the scraped version had to use. The two namespaces cannot collide and
a log line says which source an id came from. A frame with neither is an error.

**The split.** Inserted, updated, unchanged, computed against the existing keys
before the write. "Unchanged" means the key exists and none of `date_unix`,
`status`, `home_goals`, `away_goals`, `attendance` differ; `ingested_at` is
excluded on purpose because it changes every run. A batch is deduplicated on
`match_id` (last row wins) before it reaches the database.

**Retries.** Connection errors, a body cut off mid-transfer
(`ChunkedEncodingError`, `ContentDecodingError`) and 5xx are retried with backoff;
401, 403, 404 and other 4xx are not. The first attempt against an unarchived
request with no key raises `NoSubscriptionError` before any network call.

**Two refusals in the loader.** A natural-key `match_id` is never built from a null
part: a row missing its date or a club string raises rather than hashing the word
`None` into a key that would collide with every other such row. And a frame that
carries the same field under both its API name and its raw name (`homeID` and
`home_raw`, say) is refused rather than silently picking one, because that is what
a half-converted second source looks like.

## Phase 02 - the lock

**Route: retry, not swap.** `connect_for_write` retries a held lock with
exponential backoff (2, 4, 8, 16 seconds at the defaults) and then raises
`DatabaseLockedError` naming the holding executable and PID, which DuckDB reports.
`commit_and_swap` was deleted.

Why not swap:

- DuckDB is transactional. `CREATE OR REPLACE TABLE` commits atomically and a
  crash mid-write is rolled back from the WAL on the next open. "A reader sees the
  complete old state or the complete new state" is the engine's guarantee.
- A swap does not fix the failure it is meant to fix. If Tableau has the file open
  on Windows, `os.replace` fails on the open file; and DuckDB refuses a read-only
  open while a writer holds the lock in any case. Only the holder letting go
  helps, so wait a bounded time for that, then say who it is.
- Only lock errors are retried. A corrupt file or missing directory raises on the
  first attempt.

**What the run log records.** Nothing, and that is stated plainly in the error:
the run log is a table inside the locked database. The failure goes to the file
log under `logs/`, to stderr, and to the exit code, which `scripts/run_weekly.*`
propagate to the scheduler. The D1 demo points at that log line.

## Phase 03 - club identity

`raw_name` in `club_aliases.csv` holds the provider's numeric id as text, plus one
row per display name. The join key is normalised on both sides by
`normalize_club_key` (whitespace collapse only) and its SQL twin, so `93` and
`"93"` cannot become different keys.

**Display names are per club-season.** `club_conference.csv` gained a
`display_name` column. The guide flags that the API's name is the club's current
name, so a 2017 match would render under a 2026 brand; a name that lives on the
club-season row is the slowly-changing-dimension shape that handles a rebrand
without touching history.

**The USL reference rows.** Written before a USL match was archived, so the
subscription month starts with the mapping layer in place rather than being spent
on it. `club_conference.csv` holds every USL Championship club-season from 2017 to
2025 with the conference and the display name of that year, `conference_structure.csv`
the playoff line of every conference-season, and `club_aliases.csv` a name row per
club covering its current and former names. The provider's numeric ids are not
knowable in advance; `scripts/propose_aliases.py` derives those rows from the
archive through the name rows, loosely matched for the proposal and exactly joined
afterwards. Three things make a from-memory conference list safe to run: the
existing checks name a club the list missed or a club it includes that never
played; a new check, `conference_membership_is_plausible`, names a club filed under
the wrong conference from its fixture list, which is the one error nothing else
could see; and `tests/test_reference_data.py` pins the size of every
conference-season so an accidental edit is caught. 2021 is filed by division
(Atlantic, Central, Mountain, Pacific) because that is the field a club was ranked
against and the line it chased; 2020's COVID groups are approximated at the
conference grain. 2026 is left for when its field is settled.

## Phase 04 - standings

**Conference rank, full field.** Exercise 4.2 is resolved in favour of the full
field: `int_standings` has one row per club in the conference for every date any
club in that conference plays, so `rank_before` on a Wednesday is the number a fan
would recognise, not "rank among the three clubs in action".

**Point-in-time via a strict ASOF join.** Running totals are computed *including*
each match, and every grid row is joined to the club's most recent match strictly
before the grid date. For a club's own match date that is its previous match,
which is exactly `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING`; for a date
the club does not play it is the carried-forward table, which the window form
cannot produce. The `no_future_leakage` check recomputes `pts_before` by a
different method (a non-equi join over played matches strictly before the row's
date) and compares.

**Tie-breaking.** Points, goal difference, goals for, with `RANK()`, so genuine
ties share a position. Head-to-head is not implemented; the occasional shared
rank is accepted.

**Match date and timezone.** The API sends kick-off as UTC unix seconds. The match
date is taken in `config.MATCH_TZ`, default `UTC`, which is exact for the example
season. For USL it is a judgement call: a 7:30pm Pacific kick-off is already
Sunday in UTC, which would put `is_weekend` wrong for every west-coast Saturday
match. Set `MATCH_TZ` to a US zone before pointing the pipeline at USL data, or
extend `stadiums.csv` with a per-club zone and join it in `stg_matches`.

## Phase 05 - the SQL layer

Seven models, in order: `stg_clubs`, `stg_matches`, `stg_weather`, `int_standings`,
`int_stakes`, `mart_match_features`, `mart_decay_curve`. The guide's four plus
three: `int_stakes` keeps the playoff-line arithmetic out of the mart so it can be
read on its own, `mart_decay_curve` is the dead-rubber curve as a table so it can
be exported and plotted, and `stg_weather` (phase two) is the typed view of
`raw_weather`, created empty before the weather stage has ever run so the mart's
join is always well-formed.

Tunables reach the static SQL through a one-row `ref_config` table (COVID window,
match timezone, relegation assumption, playoff fallback) built by
`usl/transform/reference.py`, so nothing is string-formatted into SQL.

Checks: the seven the guide lists, plus eight that mutation testing and review
showed were needed. Three on the match data: `one_match_per_club_per_date` (a
doubleheader or a double-ingested season silently corrupts the standings window),
`all_club_seasons_have_conference` (a club-season missing from
`club_conference.csv` would otherwise drop out of `int_standings` on an inner join
with no error), and `played_rows_consistent` (a raw row with a parseable score and a
gate but a status other than `complete` - status drift at the provider - and any
status outside `config.KNOWN_MATCH_STATUSES`, so a new spelling is named on the
run it first appears). Four on the reference CSVs, which are the one input nothing
upstream validates: `one_conference_per_club_season` (a club-season listed twice
produces no null anywhere; it inflates `n_clubs`, shifts both lines, and doubles
that club's mart rows), `all_conference_clubs_have_fixtures` (a club pasted under a
season it never played sits in the table on zero points and moves the relegation
line; when `all_clubs_mapped` is also failing the hint says to fix that first, since
an unmapped club string leaves its club-season fixtureless too),
`conference_structure_is_well_formed` (a duplicated pair, a non-numeric spot count
that `TRY_CAST` would quietly turn into the default, or more spots than clubs), and
`derby_clubs_are_known` (a typo in `derbies.csv` is otherwise a derby that never
fires). And one on the fixture list against the CSV:
`conference_membership_is_plausible` fails a club-season that plays more fixtures
against other conferences than its own, because a club filed under the wrong
conference is present, mapped, playing, listed once, and ranked against the wrong
field on every date. Two more with phase two: `home_matches_resolve_to_one_stadium`
(a gap or an overlap in `stadiums.csv` validity ranges, or a club with no row,
each of which drops or duplicates weather silently) and `played_weather_is_observed`
(a played match older than the archive lag still carrying forecast weather, which
is training data quietly containing predictions). Seventeen in all, collected
within a tier, stopped between tiers, every result logged. Hints name the file and
the row to change, and a check whose failure would send you to the wrong file
says so.

A conference-season with no fixture at all is reference data written ahead of the
data, not a phantom: the standings grid takes its dates from fixtures, so it has
no rows and moves no line. `all_conference_clubs_have_fixtures` reports those and
fails only a fixtureless club in a conference that is playing. That is what lets
the USL rows sit in the CSVs while the example season runs.

**A DuckDB 1.5.3 binder bug, worked around in the SQL.** `int_stakes` originally
took the points on each line with `MAX(CASE WHEN position = playoff_spots THEN
pts_before END) OVER (PARTITION BY ...)` inside a chained CTE. DuckDB 1.5.3 alone
fails to bind that with an INTERNAL "failed to bind column reference" error; 1.3.2,
1.4.5, 1.5.0 to 1.5.2, 1.5.4 and 1.5.5 all run it. The line points are now a grouped
aggregate joined back on the table-date, which every release binds the same way
and which is the plainer statement of the intent anyway. Same rows, same values;
the standings and feature tests did not change. If a query ever fails with an
INTERNAL error, check the DuckDB version before the query.

## Phase 06 - features

- **Lag windows cross season boundaries, not holes.** A club's first home match
  of a season takes its moving average from the end of the previous one. Support
  level carries over; the alternative leaves every season opener with null lags.
  It does not carry over a gap longer than `config.LAG_MAX_GAP_DAYS` (see the
  subscription-month section): after a hole in the record the history restarts.
- **COVID is handled before lags.** Lag features are computed over played,
  non-COVID home matches only, then joined to every match, so a 2021 club's moving
  average is not dragged toward empty-stadium figures.
- **`is_mathematically_live`** compares `pts_before + 3 * matches_remaining` against
  the current points of the club in the last qualifying position, strictly greater.
  It is an approximation (no head-to-head constraint solving) and it is
  conservative in the right direction for the decay curve.
- **`matches_since_elimination`** counts the club's home matches since the first
  date it was no longer live: 0 for the first such match, and `-1` while the club
  is still live. A sentinel rather than a null, so the column is never null and the
  decay curve's `>= 0` filter reads naturally.
- **The playoff line** comes from `usl/ref/conference_structure.csv` keyed by
  `(season, conference)`; the relegation line from the same file where filled in,
  else `config.ASSUMED_RELEGATION_SPOTS` (bottom two). For the EPL row both are real.
- **Unplayed fixtures are in the mart** with `attendance` null and
  `is_played = false`, because forecasts for remaining home matches need their
  features. Training and metrics use played rows only.
- **A void fixture is kept in staging and counts for nothing.** `is_played` is
  `status = 'complete'` with a parseable score, and `is_void` is a status in
  `config.VOID_MATCH_STATUSES` (`canceled`, `cancelled`; the list reaches the SQL
  through `ref_config`). A void row stays in `stg_matches` so `row_count_preserved`
  still holds, but it is excluded from the standings grid, from
  `matches_remaining`, from the home-match sequence and from the mart: it is not a
  fixture anyone will play, so it must not lengthen the season or become a
  forecast. `postponed` and `suspended` are not void - they are usually
  rescheduled, and the row's date moves when they are. The
  `mart_matches_staging` check compares the mart to the non-void staging count and
  reports the void rows separately.
- **Null policy.** The `features_not_null` check fails the run on any null outside
  `config.ALLOWED_NULL_FEATURES` (the four lag features). Nulls inside that set go
  to XGBoost, which learns a default direction. No imputation.

## Phase 07 - models

- `opponent_club_id` is passed as a pandas categorical with the category set fixed
  to every club in the mart, `enable_categorical=True`, so codes cannot drift
  between train and test.
- The primary metrics are a single chronological holdout (`TEST_FRACTION`), which
  is what the schema supports. Two stronger views are written alongside:
  `model_cv` holds expanding-window folds by season (empty with one season), and
  `model_variance` holds MAE per seed across `config.VARIANCE_SEEDS`, so the
  A-to-B gap can be read against run-to-run noise as exercise 7.2 requires.
- The naive club-mean baseline is written as `model_name = 'naive_club_mean'`. It
  is the club's mean home gate in the same season's training rows first, then the
  club's mean across every training season, then the training-set mean. Per-season
  first because a club's gate moves with promotion, a new ground, or a cup run,
  and a baseline that averages 2016 with 2024 is easier to beat than the one a
  ticketing office would actually use. The bar is deliberately the harder one.
- A played match with no gate in the source is neither trained on nor forecast.
  It is not a future fixture, so a forecast for it would be a row Tableau shows as
  upcoming for a match already played; and it has no target, so it cannot be a
  training row. The training summary counts these as `n_no_gate` so a season
  where the provider stopped reporting attendance is visible in the run log.
- `XGB_PARAMS` carries `subsample` and `colsample_bytree` at 0.8, not for accuracy
  but so the seed does something: without subsampling the hist booster is fully
  deterministic and `model_variance` reads a spread of exactly zero across every
  seed, which would make the noise floor of exercise 7.2 a fiction.
- Forecasts for unplayed fixtures are produced by a refit on all played rows and
  written with `actual` null.
- The uncertainty band for the drill-down view is historical residuals, labelled as
  such: `predicted` plus or minus the run's MAE, joined from `model_metrics`. The
  export writes `predictions_with_band.csv` with that join done.

**What the example season showed.** The naive club mean beat both XGBoost models on
the holdout (MAE 998 against 1,207 and 1,204), the gap between the two models was
three attendees against a seed spread of hundreds, and the decay curve was flat at
about 1.02. All three are the expected result of one season of data and are written
up as such in the README. The pipeline's job on this season was to be correct, not
to be right.

## Phase 08 - Tableau

Tableau is not installed in the environment this was built in. The export path is
complete and exercised; the workbook itself is a manual step. `tableau/README.md`
carries the view-by-view spec against the exported CSVs.

## Phase 09 - demos

All seven scripts run from the archive with no key and restore state in `finally`.
D4 snapshots the database file before injecting the null and copies it back after.
D3 edits `club_aliases.csv` in place and restores it byte for byte.

## Phase 11 - Dagster

- **Thin wrappers, one file per concern.** `usl/assets/` holds one asset per raw
  table, reference CSV and SQL model, a multi-asset for the five model tables, and
  one for the extracts. Each body is the phase-one call plus a metadata dict; no
  logic lives there, and a test checks that each SQL asset's declared upstream is
  exactly the tables its file reads.
- **Checks block by tier.** Every check function becomes a blocking asset check
  through one factory, anchored on `stg_matches`, `int_standings` or
  `mart_match_features` by tier. A check may name upstream assets as extra
  dependencies, never a downstream one (a cycle), which is why the mart checks
  read nothing beyond the mart.
- **Serial, in one process.** `Definitions(executor=in_process_executor)`. DuckDB
  is single-writer and the pipeline takes seconds; concurrency would reopen the
  phase-02 problem for no gain. Each asset opens its own connection through the
  same lock guard as the CLI.
- **The run log is written under the Dagster run id**, one row per asset as a
  stage, and every asset check writes its `check_log` row. The Tableau tracker
  strip reads the same tables whichever scheduler is in charge.
- **Schedule** `weekly_tuesday`, `config.SCHEDULE_CRON` in `config.SCHEDULE_TZ`,
  Central by default like the match date (`USL_SCHEDULE_TZ` overrides). One
  scheduler at a time: turning the Dagster schedule on is the moment the
  scheduled task is deleted. `dagster dev` keeps its run history in a temporary
  home unless `DAGSTER_HOME` points at a directory, so set it before the first
  scheduled run or the history the UI exists for is lost at every restart.
- **Not built:** partitions, IO managers, sensors. Whole-table rebuilds into one
  DuckDB file need none of them.

## Phase 12 - weather

- **The per-minute limit is waited out.** Open-Meteo's free tier weights a
  multi-year archive range as many requests, so the USL backfill trips its
  per-minute limit every ten grounds or so. A 429 is not a failed attempt: the
  client waits `config.WEATHER_RATE_LIMIT_WAIT_SECONDS` and sends the same
  request again, up to `WEATHER_RATE_LIMIT_WAITS` times, and every request is
  spaced by `WEATHER_REQUEST_DELAY_SECONDS`. Each response is committed to the
  archive as it lands, so a run that does stop resumes where it left off.
- **Archive-first, the FootyStats way.** Open-Meteo responses go through
  `usl.ingest.archive`: `.partial`, validate (JSON, no error envelope, a daily time
  series), atomic rename, `.bad` on failure. Observed weather is fetched once per
  club, ground and date range; a forecast is a dated snapshot per day it is made.
- **A rebuilt database is served from the archive by coverage.** An observation
  file is keyed by the range that was missing when it was fetched, so the exact
  key a fresh database computes (one range per club across every season) was
  never archived, and the first rebuild after the 2026 top-up went to the
  network for weather the archive already held. The refresh now replays every
  archived observation file whose range covers a date the club is missing,
  reading the coordinates and the range off the filename, and requests only the
  dates no file covers. Two counts in the run log say which happened,
  `weather_archive_replayed` and `weather_archive_requests`; `force=` skips the
  replay so a file can be refreshed on purpose.
- **What needs weather is decided from staging**, so the staging tier is rebuilt
  at the start of the weather stage. Needs join `stadiums.csv` on the club and the
  validity range covering the match date (exercise 12.1). One archive request per
  club and ground spans the earliest to the latest played home date still without
  an observation, clipped to `config.WEATHER_ARCHIVE_LAG_DAYS` before today; one
  forecast request per club and ground covers the unplayed fixtures inside
  `config.WEATHER_FORECAST_DAYS`, and only the fixture dates are written.
- **Observation over forecast, always.** A forecast never overwrites an
  observation; an observation replaces a forecast, counted as an upgrade; and a
  played match older than the lag that still carries a forecast fails the
  `played_weather_is_observed` check. `weather_source` and `weather_horizon_days`
  ride into the mart as non-feature columns.
- **Coordinates at city level**, one row per club, split only where a club
  actually moved city or ground across a metro (Tottenham, Louisville City,
  Bethlehem to Chester, Tukwila to Tacoma). Daily weather does not vary across a
  metro, and a finer file is more rows to get wrong for no signal.
- **A feature null on every played row is dropped before training.** It carries
  nothing and only perturbs column subsampling. This is what keeps the example
  season's numbers stable when weather is disabled, and it also catches
  `same_fixture_last_season` on a single-season dataset, so the numbers moved
  once when the rule landed; the README carries the new ones.
- **On by default, now that the example season's weather is archived.** It was
  off while the archive-only run had nothing to serve; the backfill (21 responses,
  5,316 club-days) is committed, an enabled run with nothing missing makes no
  request, and the default flipped. `USL_WEATHER_ENABLED=0` still records a skip
  and leaves the weather columns null.
- **Weather moved the numbers and not the conclusion.** With real weather Model A's
  holdout MAE rose from 1,361 to 1,585 and Model B's fell from 1,421 to 1,415;
  cloud cover is sixth by gain in both. On one season the family is noise, and the
  README says so.
- **No neutral-site list.** Every match is treated as played at the home club's
  ground for that date; the caveat is stated in phase 12. The list goes beside
  `derbies.csv` if it is ever needed.

## The subscription month

- **Seasons in scope: USL Championship 2017 to 2026**, ids from `league-list` on
  day one and recorded in `usl/ref/seasons.csv`. 2026 is the season in progress:
  its conference rows were derived from the fixture list, it is archived to the
  day it was pulled, and `USL_CURRENT_SEASON=2026` on the machine that runs the
  weekly job makes the ingest re-pull it as a dated snapshot. 2013 to 2016 exist
  at FootyStats and are out of scope until someone writes their conference rows.
- **The example season moved to `seasons.example.csv`.** One database holds one
  league, so the EPL row left `seasons.csv`; `USL_SEASONS_CSV` points the pipeline
  at the example file, and the tests and demos set it, along with no key and no
  current season, so a paid key in a developer's `.env` can never reach the
  network from a test.
- **Match date in US Central.** One zone for the league rather than a per-club
  column: a 7:30pm Pacific kick-off is 9:30pm Central and still the same day, a
  7pm Eastern one is 6pm Central, and only a Pacific kick-off at or after 10pm
  would cross midnight, which the league does not schedule. Exact for the example
  season as well, whose latest kick-off is 20:00 UTC. The per-club column stays
  an option if a neutral-site match ever needs it.
- **What the archive said about attendance.** 2017 to 2019 carry a gate on 94 to
  100 percent of matches; 2020 on 12 percent (COVID); 2021, 2022 and 2023 on none
  at all (`-1` on every row); 2024 and 2025 on 57 and 61 percent, missing by
  month rather than by club. About 2,100 labelled matches in all, with a
  four-season hole in the middle. The pipeline trains on what has a gate and
  says how much that is on every run-log row; a second attendance source for
  2021 to 2023 is the open item, not a blocker.
- **Playoff matches are in the mart and out of the table.** FootyStats marks
  rounds by id and the season's largest round is the regular season, which the
  provider's own round names confirm for every archived season. A playoff match
  adds no points, is not a scheduled fixture, is not the opener or the final home
  match, does not feed the lag history, and reads the final regular-season table
  through an ASOF join; it is live by definition and carries `is_playoff` as a
  context feature, because a knockout match is the highest-stakes fixture the
  league has. The decay curve is regular season only.
- **An abandoned match is void.** One 2025 match is `incomplete` with a gate of
  2,993: it kicked off and never reached a result, and the replay two days later
  is its own row. `incomplete` with a gate recorded means abandoned, and void
  keeps it out of the table, the schedule and the mart; the replay stands alone.
  A cancelled fixture beside its replacement on the same date is not a
  doubleheader either, so that check ignores void rows.
- **Two conference corrections from the data.** The API's regular-season tables
  agree with the hand-written lists for 2017, 2019, 2021 and 2023 club for club
  and put FC Tulsa in the East for 2022 as well as 2023. For 2020, 2024 and 2025
  the API's tables are empty and the fixture list decided:
  `conference_membership_is_plausible` found Lexington SC playing 22 of 30
  against Western clubs in 2025, so the league balanced that season at twelve a
  side, not thirteen and eleven. Both rows carry the correction in their note.
- **The lag history restarts after a gap.** With no gates for 2021 to 2023, a
  2024 opener would have inherited its club's 2019 crowd as `last_home_gate`, and
  19 of 24 did before the rule. `config.LAG_MAX_GAP_DAYS` (400, one off-season
  and no more) partitions a club's gated home matches into eras that restart at
  any longer gap; the moving averages are computed within an era and a gate older
  than the limit is no lag at all. The price is null lags at the far side of a
  hole, which the allowed-null list already covers.
- **The standings are verified, not assumed.** `scripts/verify_standings.py`
  compares every club-season with the provider's published tables two ways, and
  on the ten seasons all 289 totals and all 181 regular-season group rows agree.
  The same comparison runs in `tests/test_usl_archive.py`, so a reference-file
  edit that moves a table is caught.
- **What the USL runs showed.** Both XGBoost models beat the naive club mean by
  about 550 attendees on the holdout and on every expanding-window fold. The
  A-to-B gap is inside the noise, and the runs prove it between them: on nine
  seasons without weather Model B won all four folds by 8 to 28; with the five
  weather columns in, Model A won all four by 8 to 52; on ten seasons Model B is
  six attendees better on the holdout against seed spreads of 16 and 24 while
  Model A wins all five folds by 5 to 52. The direction flips with the columns
  and the rows, which is what a gap inside the noise does. Only 69 of 2,300 gated
  regular-season home matches were played after mathematical elimination,
  because eight of twelve qualify, so the decay curve has nothing to draw; that
  absence is the thesis. The README carries the figures.
- **A gate of 8 is left in, and named.** Four of 2,386 recorded gates are under
  100 (50, 18, 2 and 8), provider errors on their face; New Mexico United v
  Orange County SC on 13 June 2026 is the 8, against a club mean above 9,000.
  One such row in the holdout lifts MAPE from 28 percent to over 200 and moves
  MAE by 15 attendees, so the README reads MAE and says why. A minimum plausible
  gate would be a modelling decision with a config constant, a check that names
  the rows and a test; it is not made here because it is the kind of choice the
  guide says to write down first.
- **Weather for every USL ground is archived, 2026 included.** 78 observation
  responses for 52 clubs and their grounds - 53 from the first backfill, which
  waited out the per-minute limit five times, and 25 for the 2026 top-up -
  92,826 observed club-days, and the forecast snapshot of 7 September for the
  fortnight ahead. Every one of the 4,463 played home fixtures older than the
  archive lag reads an observation, the 27 inside the horizon read the snapshot,
  and the archive-only run makes no request. On ten seasons the weather family
  stays minor: no weather column reaches the top ten by gain in either model.
  Rain on a match day is a real effect; it is a small one next to who is playing
  and what they drew last time.
- **2026, the season in progress, is in.** The provider's table carries no
  conference groups for a season under way, so the 2026 lists were derived from
  the fixture list the way `conference_membership_is_plausible` reads it: every
  club plays 24 or 25 of its 30 inside one group, which gives 13 East and 12 West
  with Lexington SC in the West as in 2025. Brooklyn FC and Sporting Club
  Jacksonville are new; North Carolina FC left. Eight playoff spots a side are
  assumed until the playoff rounds appear in the provider's table. Four fixtures
  are `suspended` on future dates with no gate and read as unplayed. The run
  wrote its first forecasts on this season: 94 fixtures, both models, `actual`
  null, and the drill-down band around each. Its 2026 totals match the
  provider's table to the day the season was pulled.
- **The current season is a deployment setting.** `USL_CURRENT_SEASON` in `.env`
  on the machine that runs the Tuesday job, not a committed constant: it is a fact
  about where the pipeline runs, and committing it would make every clone's
  freshness check fail in season.

## Not decided here

- Head-to-head tie-breaking.
- Whether the in-progress season is held out of training once it is loaded.
