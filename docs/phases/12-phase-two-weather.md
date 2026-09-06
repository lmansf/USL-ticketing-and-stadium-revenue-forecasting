# Phase two - Weather features via Open-Meteo

> **Status: built, off by default.** The client, the refresh, the stadium file,
> the mart join, the checks and the feature family are all in, and every one is
> tested against a fake Open-Meteo. What this environment could not do is reach
> Open-Meteo, so the observed weather for the example season is not archived yet:
> `USL_WEATHER_ENABLED=1 python -m usl.run weather` on a connected machine is the
> one manual step, after which the responses are archived and the run needs no
> network again. See [How it landed](#how-it-landed) at the end.

---

## Why Open-Meteo

Free for non-commercial use, no API key, historical archive going back decades, and a
forecast endpoint for upcoming fixtures. You pass latitude, longitude, and a date range.

That last part is the reason it is the right choice here: it drops the
stadium-to-station mapping problem entirely. You need stadium coordinates, not weather
stations, and coordinates you can look up once and check in. A station-based API would
have you maintaining a second mapping layer with the same silent-failure properties as
`club_aliases.csv` - a station that goes offline mid-history produces nulls nobody
notices.

Hand-build `stadiums.csv` once, check it in, and loop.

---

## Approach

- **Backfill:** one archive call per club covering its full date range, then join to
  home matches on date. Not one call per match - that is thousands of requests for data
  you could get in a few dozen.
- **Weekly:** archive for last week's matches, forecast for the next fixtures.
- **Fields:** daily max and min temperature, precipitation total, wind, cloud cover.

Cache the archive responses. Historical weather never changes, so re-requesting it on
every run is pure waste - the same reasoning as the completed-season cache in
[phase 01](01-ingest-to-raw.md#exercise-13---rate-limiting-and-the-archive-cache), and the same
implementation shape.

---

## Archive versus forecast

Two endpoints, two meanings, and mixing them silently is the trap here.

Archive data is observed. Forecast data is predicted, and its accuracy degrades with
horizon - a fixture ten days out has a materially worse weather input than one three
days out. A model trained entirely on archive data and then fed forecast data at
prediction time has a distribution shift baked in.

Record which source each row came from. A `weather_source` column with values `archive`
and `forecast`, plus the forecast horizon in days, costs nothing and lets you answer
"is the model worse on long-horizon fixtures" - which it will be, and which is a good
thing to know rather than to discover.

There is also a boundary case worth handling deliberately: a match played three days
ago whose weather you fetched as a forecast a week earlier should be *re-fetched* from
the archive on the next run, overwriting the forecast value. Otherwise your training
data quietly contains forecasts.

---

## Exercise 12.1 - A club that moved grounds

Handle a club whose stadium changed mid-history without corrupting its older matches.

<details>
<summary>Solution</summary>

Give `stadiums.csv` validity ranges and join on them:

```csv
club_id,stadium,lat,lon,valid_from,valid_to
some_club,Old Ground,27.94,-82.45,2017-01-01,2021-12-31
some_club,New Ground,27.96,-82.51,2022-01-01,2099-12-31
```

```sql
FROM stg_matches m
JOIN stadiums s
  ON m.home_club_id = s.club_id
 AND m.date BETWEEN s.valid_from AND s.valid_to
```

Same slowly-changing-dimension pattern you will need if a club moves between divisions
once pro-rel starts - worth mentioning in the write-up, because it is the same problem
USL will have on their own revenue history.

Add a check that every home match resolves to exactly one stadium row. An inner join
against overlapping validity ranges silently duplicates matches, and a gap between
ranges silently drops them. Both are the [phase 03](03-club-name-consistency.md) failure
mode wearing a different hat, and the fix is the same: `LEFT JOIN`, then assert.

The far-future `valid_to` of 2099 is deliberate. A null end date means writing
`(m.date >= s.valid_from AND (s.valid_to IS NULL OR m.date <= s.valid_to))` everywhere
you join, and someone will eventually forget the null branch.
</details>

---

## Neutral-site and relocated matches

A small number of matches across nine seasons are played somewhere other than the home
club's ground - hurricane relocations, stadium construction, one-off events at a larger
venue. These get the wrong weather under the join above, and they also get the wrong
attendance interpretation, because a neutral-site gate is not a home gate.

There is no flag for this in the source data. If you care, it is a hand-maintained list
in the same spirit as `derbies.csv`. If you do not, say so - "n matches over nine
seasons are neutral-site and are treated as home matches" is a fine caveat, and an
unstated one is not.

---

## Where it lands

`usl/weather/open_meteo.py` is stubbed with the client contract. `usl/ref/stadiums.csv`
is stubbed with the column headers and validity-range shape. The mart gains a weather
family in `usl/features/definitions.py`, which both models pick up automatically -
weather is a shared feature, not a pro-rel one, so it goes in the base list.

---

## How it landed

```
usl/weather/open_meteo.py   # fetch_archive, fetch_forecast: archive-first, same partial/quarantine/commit as FootyStats
usl/weather/refresh.py      # what needs weather, one request per club and ground, forecast never over observation
usl/weather/schema.py       # raw_weather, one row per club-day
usl/sql/stg_weather.sql     # the feature names and units
usl/ref/stadiums.csv        # every EPL and USL club, validity ranges where a club moved
```

```
USL_WEATHER_ENABLED=1 python -m usl.run weather    # first run: the backfill; every later run: the top-up
make weekly                                        # includes the weather stage; skipped, and logged as skipped, when the flag is unset
```

**Approach, as built.** Needs are decided from staging: every non-void home
fixture of a mapped club, joined to `stadiums.csv` on the club and the validity
range covering the match date. One archive request per club and ground covers
the earliest to the latest played home date still without an observation,
clipped to what the archive can have (`config.WEATHER_ARCHIVE_LAG_DAYS` before
today, because ERA5 trails real time by about five days). Every day of the
response is written, so a rescheduled fixture in the range already has its
row. One forecast request per club and ground covers the unplayed fixtures
inside `config.WEATHER_FORECAST_DAYS`, archived as a dated snapshot, and only
the fixture dates are written. On the example season that is twenty-one archive
requests - twenty clubs, and Tottenham twice, because they moved from Wembley
in April 2019 - for 380 matches.

**Archive versus forecast, enforced.** `weather_source` and
`forecast_horizon_days` are on every row and ride into the mart as non-feature
columns. An observation overwrites a forecast; a forecast never overwrites an
observation; and the `played_weather_is_observed` check fails the run when a
played match older than the archive lag still carries a forecast, which is the
boundary case the guide warns about, made loud.

**Exercise 12.1, done with real cases.** `stadiums.csv` has one row per club at
city-level coordinates - daily weather does not differ across a metro, so a move
within one is not split - and validity ranges where the move was real:
Tottenham (Wembley to the new ground), Louisville City (Slugger Field to Lynn
Family Stadium), Bethlehem Steel to Philadelphia Union II (Bethlehem to
Chester), Seattle Sounders 2 to Tacoma. The `home_matches_resolve_to_one_stadium`
check names a gap or an overlap per match, and a club with no row at all.

**The feature family.** `temp_max_c`, `temp_min_c`, `precipitation_mm`,
`wind_max_kmh`, `cloud_cover_pct` join the base list, so both models see them.
They are in `config.ALLOWED_NULL_FEATURES` because they are null until the
backfill is archived and null for a fixture beyond the horizon. A feature that is
null on every played row is dropped before training rather than fed to the model
as a constant: it carries nothing, and it would only perturb column subsampling.
That rule also catches `same_fixture_last_season` on a single-season dataset,
which is why the example season's numbers moved when it landed (see the README).

**Neutral-site matches.** No list is maintained. Every match is treated as
played at the home club's ground for that date, and that is the caveat: over
nine USL seasons a handful of relocated matches get the wrong weather and the
wrong attendance interpretation. If it ever matters, the list goes beside
`derbies.csv` in the same spirit.

**Why it is off by default.** The archive-only run and CI have no network, and
until the backfill has been run once on a connected machine there is no archived
weather to serve. With the flag unset the stage records that it was skipped, the
weather columns are null, and the model is exactly the model without weather.
With the flag set and everything archived, no request is made.
