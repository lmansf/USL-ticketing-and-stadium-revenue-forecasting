# Open questions

Decisions the build guide leaves open, plus places where following it literally
requires a judgement it does not make. Each one says where it is resolved in this repo,
or that it is not.

Resolving these is part of the work. Leaving them undocumented is the thing to avoid -
an interviewer probes exactly here, and "I did not notice there was a choice" is the
only bad answer.

Each question below now carries a **Resolved** note saying what was chosen; the
reasoning behind every choice is collected in [build-decisions.md](build-decisions.md).

---

## Resolved by the project scope

### Conference rank, not league-wide

The guide names this as a choice and says to write the answer down. **This project ranks
within conference.** It is the rank that determines playoff qualification, and it is the
one that will determine relegation.

Resolved in [phase 04](../phases/04-standings-as-of-match-date.md#conference-not-league-wide).
The [MVP track](../mvp/02-mvp-sql-and-features.md) uses league-wide as an explicit
simplification.

### Data source and the paid month

FootyStats, roughly 30 EUR on the entry tier, for one month only. That makes data
acquisition the project's first hard deadline and reorders the build:
[phase 00](../phases/00-data-access-and-the-clock.md) now comes before everything.

Resolved consequences: every raw response is archived to `data/raw_archive/`, which is
committed rather than gitignored; the pipeline must run end to end with no API key; and
the client is built against the free `example` key before the subscription starts.

### Orchestration

The guide uses Dagster throughout. **Phase one uses a plain weekly scheduled task**;
Dagster is deferred to phase two and documented in full at
[phase 11](../phases/11-phase-two-dagster.md). Phase-one checks are written as plain
functions returning a result object specifically so the migration is a decorator rather
than a rewrite.

### Weather

Deferred to phase two, [phase 12](../phases/12-phase-two-weather.md). Both models train
without it, and weather is a shared feature rather than a pro-rel one, so its absence
does not confound the headline comparison.

### Tableau edition

The guide's plan: build against DuckDB via the JDBC connector during the 14-day Desktop
trial, record the video then, and keep a file-based export path so the free Tableau
Public edition carries the artifact afterwards. Both paths are documented -
[phase 08](../phases/08-tableau.md) and
[tableau-duckdb-connector.md](tableau-duckdb-connector.md) for the live connection,
[MVP 04](../mvp/04-mvp-tableau.md) for extracts.

Note the constraint that makes this necessary: Tableau Public connects to files only, so
the connector is unavailable there. The two are not interchangeable.

### Scheduler platform

Windows Task Scheduler, per [MVP 05](../mvp/05-mvp-schedule.md), with a cron equivalent
noted for macOS and Linux. The scheduler is the only platform-specific piece of the
project; `scripts/` carries an entry point for each.

---

## Unresolved - you have to decide

### Conference membership

**The guide does not say where conference membership comes from**, and it is not a
constant. Clubs have moved between conferences across seasons, and the number of
conferences has itself changed over the nine seasons in scope.

`usl/ref/club_conference.csv` is stubbed as `club_id, season, conference`, which is the
shape that handles both. **Verify against the source before filling it in.** The wrong
approach is to read the conference from the current season's standings page and apply it
backwards - that is wrong for every club that moved, and it is wrong silently.

Open sub-question: what happens to `rank_before` in a season where the league did not
split into conferences, if such a season exists in your range. Ranking league-wide for
those seasons is defensible; so is excluding them. Pick one and write it down.

> **Resolved in this build.** `usl/ref/club_conference.csv` is filled per `(club_id, season)` and
> also carries `display_name`. For the example season there is one conference, the whole
> league, so the sub-question is answered: a season with no split ranks league-wide by
> putting every club in one conference. See
> [build-decisions.md](build-decisions.md#phase-03---club-identity).

### Does the API carry per-match attendance for USL?

**Unresolved, and now load-bearing: there is no fallback source.**

Attendance is the target variable. FootyStats documents aggregate attendance at team
and league level (`average_attendance_home` and similar); whether the per-match record
carries a figure, and whether it is populated for USL rather than only the major
European leagues, is undocumented and could not be verified from the public docs.

This is now a **gate rather than an open question**, because the scraper has been
deleted and nothing else can supply the target. Run
`python scripts/check_attendance_coverage.py` before subscribing, and again against a
real USL season on day one. Watch for the middle outcome - a field that exists but is
mostly zeroes - which a yes/no check scores as a pass.

If it fails, the scraper is recoverable from git history and the script prints the
commands. Recovering it means two sources joined on `season + date + club`, since they
share no key.

> **Half resolved.** For the example season the `league-matches` record carries `attendance`
> populated on 380 of 380 matches (median 31,957). `scripts/check_attendance_coverage.py`
> now serves from the archive and passes offline. **The USL half is still the gate** and
> needs the subscription: run the script with `--season-id` on day one.

### Season ids

You cannot request a year from FootyStats, only a `season_id`. The mapping lives in
`usl/ref/seasons.csv` and comes from the `league-list` endpoint.

**This is only discoverable while subscribed.** An id you never recorded cannot be
looked up afterwards. Fill the file in on day one.

Related: whether the current in-progress season is in the training set at all, or held
out.

> **Resolved in shape, not in content.** `seasons.csv` lists the EPL example (1625) and the
> ten USL seasons with blank ids; the backfill skips and reports blanks. The in-progress
> season is trained on like any other and its unplayed fixtures get forecasts; hold it out
> by leaving it out of the file.

### League selection on the entry tier

The roughly 30 EUR tier covers a limited number of leagues that you select, not
everything. USL Championship being absent from `league-list` is a selection problem
rather than a coverage problem. Unverified: whether selecting a league grants all of its
historical seasons or whether each season counts separately against the allowance. Check
before assuming the nine-season backfill is possible, because the answer changes the
plan for the month.

### The undocumented match-detail endpoint

It answers, which is how it was found, but it carries no contract: no versioning
promise, no deprecation notice, no guarantee the field set is the same for USL as for
the leagues it was presumably built against. That is a reason to archive its responses
aggressively, not to avoid it. Assume it can change or vanish without notice, and make
sure nothing in the pipeline needs to call it again after the archive is complete.

### The playoff line

`points_from_playoff_line` needs a cutoff position, and the number of playoff qualifiers
per conference has changed across the nine seasons. Hardcoding one number is wrong for
some seasons. Either put it in a `season, conference, playoff_spots` reference file, or
derive it from published results, or restrict the feature to seasons where you are sure.

> **Resolved.** `usl/ref/conference_structure.csv`, keyed by `(season, conference)`, with
> `config.DEFAULT_PLAYOFF_SPOTS = None` so a missing row stops the run rather than guessing.
> See [build-decisions.md](build-decisions.md#phase-06---features).

### The relegation line

There is no relegation, so there is no line. `points_from_relegation_line` requires
inventing a cutoff - bottom two, bottom three, bottom 10 percent - and USL has not
published the 2028 structure in enough detail to pick one.

This is fine, and it is exactly why the feature is classified as instrumented and
unvalidated in [the honesty note](../phases/06-features.md#the-honesty-note). Put the
assumed cutoff in `config.py` where it is visible, and state it wherever the feature
appears.

> **Resolved.** `config.ASSUMED_RELEGATION_SPOTS = 2` where `conference_structure.csv` leaves
> `relegation_spots` blank. The EPL row has the real value, 3, so the instrument has ground
> truth on the example season only.

### The COVID window

`is_covid_affected` is a date range and the boundaries are a judgement call, not a fact.
Restrictions eased at different times in different markets, and some 2021 matches were
capacity-limited. The range lives in `config.py`; say in the README that it is a range
you chose, and be able to show the model's error with and without the exclusion.

> **Resolved.** `config.COVID_START = 2020-03-01`, `COVID_END = 2021-06-30`, reaching SQL via
> the `ref_config` table. `DROP_COVID` defaults on. Lag features skip COVID matches.

### Derbies

`usl/ref/derbies.csv` is hand-flagged, which means someone decides what counts. Two
clubs in the same metro is easy. Two clubs three hours apart with a history is a
judgement. Whatever rule you use, write it in the file's `note` column so the decision
is recoverable.

> **Resolved.** Rule recorded in the first row of `derbies.csv`: same city or metro area, or
> both clubs market the fixture as a derby.

### Tie-breaking beyond goals for

Points, then goal difference, then goals for handles almost everything. Genuine ties
beyond that exist and USL's published tie-breakers include head-to-head. Whether to
implement that or accept the occasional `RANK()` tie is a call; accepting it is
reasonable, provided you use `RANK()` rather than `ROW_NUMBER()` so tied clubs share a
position instead of being ordered arbitrarily by whatever the engine felt like.

> **Resolved.** `RANK()`, ties share a position, no head-to-head. Line positions for the
> stakes features use `ROW_NUMBER()` over the same ordering so a line always exists.

### Neutral-site matches

A handful of matches across nine seasons are played away from the home club's ground.
There is no flag for this in the source. They get the wrong weather in
[phase two](../phases/12-phase-two-weather.md) and arguably the wrong attendance
interpretation throughout. Hand-maintain a list, or state the caveat. Not stating it is
the only wrong answer.

> **Not resolved.** No list is maintained; the caveat stands as stated. The example season
> has none.

### match_id and rebrands

`match_id` hashes the raw club strings because it has to be computable at load time,
before the alias mapping runs. Consequence: if the source retroactively renames a club,
that club's historical `match_id` values change and the upsert inserts duplicates rather
than updating.

Options: accept it and detect it with a row-count check, or re-key on canonical ids in a
second pass, or version the raw table. Not addressed in the guide. Decide before it
happens rather than after.

> **Resolved, and the premise changed.** `match_id` is the provider's id (`fs:` + id), which
> does not move on a rebrand. The natural-key hash survives only as the `nk:` fallback for a
> source with no id. See [build-decisions.md](build-decisions.md#phase-01---the-raw-table-and-match_id).

### Lag windows across season boundaries

Should `home_gate_ma3` for a club's first home match of a season include matches from
the previous season? Signal (support level carries over) or noise (five-month gap,
different squad). Flagged in
[phase 06](../phases/06-features.md#exercise-61---lag-features-without-leakage); not
decided here.

> **Resolved.** They cross. Partition by club only. See
> [build-decisions.md](build-decisions.md#phase-06---features).

### Null policy

XGBoost handles nulls natively; imputing and failing are both also legitimate. What is
not legitimate is not knowing which one your pipeline does. This is demo scenario D4,
and the demo is about explaining the choice rather than about the null being a bug.

> **Resolved.** Fail the run on any null outside `config.ALLOWED_NULL_FEATURES`; pass the
> allowed ones (the four lag features) to XGBoost unimputed. Demo D4 shows both halves.
