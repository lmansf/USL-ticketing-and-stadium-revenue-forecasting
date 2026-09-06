from pathlib import Path
import json

class SchemaDriftError(Exception):
    pass

class CoverageError(Exception):
    pass

REQUIRED = {"id", "home_name", "away_name", "stadium_name", "attendance"}

def null_coverage(dataset, required):
    total = len(dataset)
    counts = {}
    for field in required:
        present = sum(1 for d in dataset if d.get(field) not in (None, "", -1))
        counts[field] = (present, total)
    return counts

# The archived response moved to data/raw_archive/ (phase 00: the archive is the
# durable copy). Read it from there.
ARCHIVE = Path(__file__).resolve().parents[3] / "data" / "raw_archive" / "league-matches_season_id_1625.json"
with open(ARCH, "r", encoding="utf-8") as file:
    content = json.load(file)

dataset = content["data"]

for d in dataset:
    missing = REQUIRED - set(d.keys())
    if missing:
        raise SchemaDriftError(f"missing {sorted(missing)}; found {sorted(d.keys())}")

for field, (present, total) in null_coverage(dataset, REQUIRED).items():
    print(f"{field}: {present}/{total}")

for d in dataset:
    print(f"Match id: {d['id']}  {d['home_name']} v {d['away_name']}  att={d['attendance']} sta={d['stadium_name']}")
		# print(f"Match id: {d['id']}")
		# print(f"Home Team: {d['home_name']}")
		# print(f"Away Team: {d['away_name']}")
		# print(f"Stadium: {d['stadium_name']}")
		# print(f"attendance: {d['attendance']}")
		# print("")		
