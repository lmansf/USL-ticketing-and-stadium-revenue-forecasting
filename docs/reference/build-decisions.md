# Build decisions

The guide leaves choices open on purpose and says, each time, "write it down".
This is where they are written down. Every decision below names the phase or the
open question it answers, what was chosen, and why. Where the choice was made
because of the data actually available at build time - the free example season
rather than nine seasons of USL - that is said too.

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

Six models, in order: `stg_clubs`, `stg_matches`, `int_standings`, `int_stakes`,
`mart_match_features`, `mart_decay_curve`. The guide's four plus two:
`int_stakes` keeps the playoff-line arithmetic out of the mart so it can be read
on its own, and `mart_decay_curve` is the dead-rubber curve as a table so it can
be exported and plotted.

Tunables reach the static SQL through a one-row `ref_config` table (COVID window,
match timezone, relegation assumption, playoff fallback) built by
`usl/transform/reference.py`, so nothing is string-formatted into SQL.

Checks: the seven the guide lists, plus seven that mutation testing and review
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
fires). Fourteen in all, collected within a tier, stopped between tiers, every
result logged. Hints name the file and the row to change, and a check whose
failure would send you to the wrong file says so.

## Phase 06 - features

- **Lag windows cross season boundaries.** A club's first home match of a season
  takes its moving average from the end of the previous one. Support level carries
  over; the alternative leaves every season opener with null lags.
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

## Not decided here

- Head-to-head tie-breaking.
- A per-club timezone for the match date.
- Which USL seasons are in scope, and whether the in-progress season is held out.
  These need the subscription and are listed in the README under "what you have to
  do by hand".
