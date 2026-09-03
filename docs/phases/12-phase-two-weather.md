# Phase two - Weather features via Open-Meteo

> **Status: deferred.** Phase one ships without weather features. Both models train
> and both answer the headline question without them; weather affects attendance but
> it affects both models identically, so it does not confound the comparison. This
> document is future work, kept in full.

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
[phase 01](01-scrape-to-raw.md#exercise-13---fetch-politeness-and-caching), and the same
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
