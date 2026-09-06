# Reference data

Six hand-maintained CSVs. All small, all checked into git, all load-bearing.

**Treat them as code, not data.** They are reviewed in diffs, a change to one
changes model output, and there is no other record of the reasoning behind their
contents than the `note` column.

| File | What it maps | Doc |
|---|---|---|
| `seasons.csv` | Season year to FootyStats `season_id`. **Only discoverable while subscribed** | [phase 00](../../docs/phases/00-data-access-and-the-clock.md) |
| `club_aliases.csv` | Every club identifier ever seen (provider id or display string) to a canonical `club_id` | [phase 03](../../docs/phases/03-club-name-consistency.md) |
| `club_conference.csv` | `club_id` and season to conference, plus the display name for that club-season | [phase 04](../../docs/phases/04-standings-as-of-match-date.md) |
| `conference_structure.csv` | Season and conference to playoff spots and relegation spots | [phase 06](../../docs/phases/06-features.md), [open questions](../../docs/reference/open-questions.md#the-playoff-line) |
| `derbies.csv` | Club pairs flagged as derbies, with the rule used | [phase 06](../../docs/phases/06-features.md) |
| `stadiums.csv` | `club_id` to coordinates, with validity ranges | [phase 12, deferred](../../docs/phases/12-phase-two-weather.md) |

All six are loaded by `usl/transform/reference.py` with every column as text and
every value whitespace-normalised, so `93` and `"93"` are the same join key. Typing
happens in SQL.

## What is in them right now

The twenty clubs of the **English Premier League 2018/19** - the season the free
`example` key serves and the one the pipeline was built against. It is not a USL
season, and the file notes say so on every row. The single conference for that
season is the whole league, so ranking within conference and ranking league-wide
coincide; the two-conference case is covered by `tests/test_standings.py` on a
fixture.

`seasons.csv` also lists the ten USL Championship seasons in scope (2017 to 2026)
with an empty `season_id` and a `TODO` note each. The backfill skips those rows and
reports them, so the file doubles as the "what have I not pulled yet" list during
the subscription month.

`stadiums.csv` still holds the phase-two example rows.

## Rules

**`club_id` is stable forever.** A club that rebrands keeps its `club_id` and
gains a new `raw_name` row in `club_aliases.csv`. Rewriting the id severs that
club's history and every lag feature that depends on it.

**Display names live on the club-season row.** `club_conference.csv` carries
`display_name` per `(club_id, season)`, because the API's name for a club is its
*current* name and a 2017 match should not render under a 2026 brand. Same
slowly-changing-dimension shape as conference membership.

**Fill the `note` column.** Record why a row exists: folded, renamed, relocated,
short form seen only on the standings page, the rule you used to call something a
derby. In two years this is the only record of that reasoning, and the person
reading it will be you.

**Unmapped means the pipeline stops.** The staging join is a `LEFT JOIN` followed
by a null check that names the offending strings. It is not an inner join, which
would drop the rows and tell you nothing. A club-season missing from
`club_conference.csv` stops it too, by a separate check, because the standings
join would otherwise drop it silently.

**`seasons.csv` is the urgent one.** You cannot request a year from FootyStats, only
a season id, and the mapping comes from the `league-list` endpoint. Fill it in during
the subscription month - afterwards there is no way to look up an id you never
recorded. **Before backfilling USL, delete the EPL row and run `make clean-db`** so
one database holds one league.

**`conference_structure.csv` needs a row per season and conference.** A season with
no row and no `config.DEFAULT_PLAYOFF_SPOTS` produces null stakes features, and the
`features_not_null` check stops the run naming them. That is deliberate: a playoff
line nobody looked up should not be guessed. Leave `relegation_spots` blank for USL
rows and `config.ASSUMED_RELEGATION_SPOTS` applies; it is filled in for the EPL row
because relegation there is real.
