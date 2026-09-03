# Phase 03 - Club name consistency

**Goal.** Every raw club string that has ever appeared in nine seasons of source data
maps to a canonical `club_id`, and anything unmapped stops the pipeline instead of
quietly vanishing.

**Why this gets its own phase.** It looks like a lookup table. It is a maintained
mapping layer over nine seasons of rebrands, relocations, and folds, and when it is
wrong it fails silently - which is the failure mode that actually bites BI teams.

**MVP cut.** Same CSV, same failing join. The MVP does not skip this, because skipping
it produces a model trained on a dataset missing clubs nobody noticed were missing.

**Files.** `usl/ref/club_aliases.csv`, `usl/sql/stg_clubs.sql`, `usl/sql/stg_matches.sql`,
`usl/transform/checks.py`

---

## The problem

Nine seasons of USL Championship include clubs that renamed, clubs that relocated,
clubs that folded, and clubs whose name is rendered differently on different pages of
the same site. Left unhandled these break joins silently: the club is simply absent
from a season, no error fires, and the row count drops by a number nobody is watching.

A relegation-era dataset will make this worse, not better - clubs moving between
divisions is the same slowly-changing-dimension problem with higher stakes.

---

## The approach

A checked-in `club_aliases.csv` mapping every raw string ever seen to a canonical
`club_id`, plus a staging join that **fails** on anything unmapped.

```csv
raw_name,club_id,note
Tampa Bay Rowdies,tampa_bay_rowdies,
Rowdies,tampa_bay_rowdies,short form
Ottawa Fury FC,ottawa_fury,folded 2020
```

Three rules for the file:

1. **It is code.** It is checked into git, it is reviewed in diffs, and a change to it
   changes model output. Treat it accordingly.
2. **`club_id` is stable forever.** A club that rebrands keeps its `club_id` and gains
   a new `raw_name` row. If you rewrite the id, you sever that club's history and
   every lag feature that depends on it.
3. **The `note` column is for humans.** Record why a row exists - folded, renamed,
   relocated, short form seen on the standings page only. In two years this is the
   only record of that reasoning.

---

## Exercise 3.1 - Fail on unmapped clubs

Write the staging step so a new or renamed club stops the pipeline instead of quietly
vanishing. The check should hand you the exact strings to add to the CSV.

Before you write it: which join type gives you that, and which one hides the problem?

<details>
<summary>Solution</summary>

```sql
-- stg_matches.sql
WITH mapped AS (
    SELECT r.*,
           h.club_id AS home_club_id,
           a.club_id AS away_club_id
    FROM raw_matches r
    LEFT JOIN club_aliases h ON r.home_raw = h.raw_name
    LEFT JOIN club_aliases a ON r.away_raw = a.raw_name
)
SELECT * FROM mapped;
```

Then a check that refuses to pass on nulls:

```python
unmapped = con.sql("""
    SELECT DISTINCT home_raw AS name FROM stg_matches WHERE home_club_id IS NULL
    UNION
    SELECT DISTINCT away_raw FROM stg_matches WHERE away_club_id IS NULL
""").df()

if len(unmapped):
    raise ValueError(
        f"unmapped clubs - add to club_aliases.csv: {unmapped['name'].tolist()}"
    )
```

A `LEFT JOIN` plus a null check beats an `INNER JOIN` here. The inner join drops the
rows and tells you nothing; this hands you the exact strings to paste into the CSV.

Log the mapped row count every run too. That number is what catches the silent-drop
case, and it is the basis of demo scenario D3.
</details>

---

## Exercise 3.2 - Normalisation before mapping

The source renders the same club as `Tampa Bay Rowdies`, `Tampa Bay Rowdies ` (trailing
space), and `Tampa  Bay Rowdies` (double space) on different pages. You could add three
rows to the CSV. Decide whether you should, and implement whichever answer you pick.

<details>
<summary>Solution</summary>

Normalise before the join, and store the normalised form in the CSV:

```python
def normalize_club_name(raw: str) -> str:
    """Collapse whitespace and strip. Nothing else."""
    return " ".join(raw.split())
```

Whitespace and casing differences are transport noise, not real aliases, and putting
them in the CSV buries the genuine rebrands in a pile of near-duplicates. Anything
beyond whitespace and case - dropping "FC", stripping accents, fuzzy matching - is a
different call, and the answer is usually no. Aggressive normalisation collides
distinct clubs, and a collision here is the exact silent failure this phase exists to
prevent. A near-miss you have to add to the CSV by hand costs thirty seconds; a
collision costs you a corrupted feature you may never find.

Apply the same function on both sides of the join, and in the loader that reads the
CSV, so the two can never drift.
</details>

---

## Row-count logging

Every run, log the count of rows entering staging and the count leaving it. Equal is
correct. A drop means rows were lost, and because the failing check above only catches
*nulls*, you want the count as a second, independent signal - a mapping that silently
points two different clubs at one `club_id` produces no nulls at all.

This log line is what you point at in demo scenario D3.

---

## What "done" looks like

- Every distinct club string across every configured season appears in
  `club_aliases.csv`.
- Editing one `raw_name` to something wrong causes the run to fail, naming that exact
  string.
- Staging row count equals raw row count, and both are logged.
- `tests/test_club_mapping.py` passes.

Next: [phase 04 - Standings as of match date](04-standings-as-of-match-date.md).
