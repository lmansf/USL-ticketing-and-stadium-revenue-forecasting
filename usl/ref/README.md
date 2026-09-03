# Reference data

Four hand-maintained CSVs. All small, all checked into git, all load-bearing.

**Treat them as code, not data.** They are reviewed in diffs, a change to one
changes model output, and there is no other record of the reasoning behind their
contents than the `note` column.

| File | What it maps | Doc |
|---|---|---|
| `club_aliases.csv` | Every raw club string ever seen to a canonical `club_id` | [phase 03](../../docs/phases/03-club-name-consistency.md) |
| `club_conference.csv` | `club_id` and season to conference | [phase 04](../../docs/phases/04-standings-as-of-match-date.md) |
| `derbies.csv` | Club pairs flagged as derbies | [phase 06](../../docs/phases/06-features.md) |
| `stadiums.csv` | `club_id` to coordinates, with validity ranges | [phase 12, deferred](../../docs/phases/12-phase-two-weather.md) |

## Rules

**`club_id` is stable forever.** A club that rebrands keeps its `club_id` and
gains a new `raw_name` row in `club_aliases.csv`. Rewriting the id severs that
club's history and every lag feature that depends on it.

**Fill the `note` column.** Record why a row exists: folded, renamed, relocated,
short form seen only on the standings page. In two years this is the only record
of that reasoning, and the person reading it will be you.

**Unmapped means the pipeline stops.** The staging join is a `LEFT JOIN` followed
by a null check that names the offending strings. It is not an inner join, which
would drop the rows and tell you nothing.

**Verify against the source.** The rows shipped here are illustrative examples of
the file format, not a researched dataset. Build the real contents by scraping
the distinct club strings from every season in scope and mapping them yourself.
