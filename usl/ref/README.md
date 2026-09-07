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
| `stadiums.csv` | `club_id` to coordinates, with validity ranges where a club moved | [phase 12](../../docs/phases/12-phase-two-weather.md) |

All six are loaded by `usl/transform/reference.py` with every column as text and
every value whitespace-normalised, so `93` and `"93"` are the same join key. Typing
happens in SQL.

## What is in them right now

Two leagues, side by side.

The twenty clubs of the **English Premier League 2018/19** - the season the free
`example` key serves and the one the pipeline was built against. It is not a USL
season, and the file notes say so on every row. The single conference for that
season is the whole league, so ranking within conference and ranking league-wide
coincide; the two-conference case is covered by `tests/test_standings.py` on a
fixture.

Every **USL Championship club-season from 2017 to 2026**: 289 rows in
`club_conference.csv` across 52 clubs, the conference and display name of the
year on each, the playoff line for every conference-season in
`conference_structure.csv`, and a name row per club (current and former names) in
`club_aliases.csv`. These were written before a USL match was archived, from the
league's published conference lists, so treat them as a careful draft rather
than ground truth:

- `tests/test_reference_data.py` pins the shape and the size of every
  conference-season, so an accidental edit is caught here.
- Once a season is pulled, the transform's checks do the rest:
  `all_club_seasons_have_conference` names a club the lists missed,
  `all_conference_clubs_have_fixtures` names a club they include that did not
  play, and `conference_membership_is_plausible` names a club filed under the
  wrong conference from its fixture list.
- The provider's numeric club ids, the join key, were derived from the archive
  by `scripts/propose_aliases.py` through the name rows: 53 USL ids, 46 matched
  by name and 7 after a name row was added by hand. Every id the ten seasons
  carry is mapped.
- The lists were verified against the provider's own tables once the seasons
  were archived: club for club for 2017, 2019, 2021 and 2023; FC Tulsa moved to
  the East for 2022 as well as 2023; and the fixture list put Lexington SC in the
  West for 2025 (22 of 30 matches against Western clubs). The corrected rows
  say so in their notes.
- 2021 was played in four divisions (Atlantic, Central, Mountain, Pacific) and
  the division is the conference in that season, because it is the field a club
  was ranked against and the line it chased. 2020's eight COVID groups are
  approximated at the conference grain; the season sits inside the COVID window
  regardless.
- 2026, the season in progress, is filled in from the fixture list, since the
  provider's table carries no groups for a season under way: 13 East and 12
  West, Lexington SC in the West as in 2025, and the rows say so in their notes.

A conference-season with no fixture at all is reported by the checks and not
failed, so the USL rows do not break the example-season run.

`seasons.csv` carries the USL Championship season ids for 2017 to 2026, pulled
from `league-list` on the first day of the subscription. 2026 is the season in
progress; its conference rows were derived from the fixture list, since the
provider's table carries no groups for a season under way, and it is 25 clubs
after North Carolina FC left and Brooklyn FC and Sporting Club Jacksonville
joined. FootyStats also has 2013 to 2016 (ids 4288, 4283, 4279, 1292),
out of scope until someone writes their conference rows. The EPL example season
lives in `seasons.example.csv`; `USL_SEASONS_CSV` points the pipeline at it, and
the tests and demos do exactly that, so one database holds one league.

`stadiums.csv` has a row for every club in `club_conference.csv`, EPL and USL, at
city-level coordinates - daily weather does not differ across a metro, so a move
within one is not split - with validity ranges where the move was real (Tottenham,
Louisville City, Bethlehem Steel to Philadelphia Union II, Seattle Sounders 2 to
Tacoma). `home_matches_resolve_to_one_stadium` names a gap, an overlap, or a club
with no row; `tests/test_weather.py` checks the committed file.

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
a season id, and the mapping comes from the `league-list` endpoint. It is filled in;
the `league-list` response is archived beside the seasons, so the ids can always be
looked up again. One database holds one league: the EPL example season is in its
own file, and `make clean-db` between leagues.

**`conference_structure.csv` needs a row per season and conference.** A season with
no row and no `config.DEFAULT_PLAYOFF_SPOTS` produces null stakes features, and the
`features_not_null` check stops the run naming them. That is deliberate: a playoff
line nobody looked up should not be guessed. Leave `relegation_spots` blank for USL
rows and `config.ASSUMED_RELEGATION_SPOTS` applies; it is filled in for the EPL row
because relegation there is real.
