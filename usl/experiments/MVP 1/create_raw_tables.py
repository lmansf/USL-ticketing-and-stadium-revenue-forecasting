from pathlib import Path
import duckdb
import json
import pandas as pd
from datetime import datetime, timezone

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

raw_tables_for_csv = pd.DataFrame(columns=["id", "home_name", "away_name", "stadium_name", "attendance","last_updated"])
for d in dataset:
    

# 1. Define the new row as a dictionary (or list of dicts)
    new_row = {"id": d['id'], "home_name": d["home_name"], "away_name":d["away_name"], "stadium_name":d["stadium_name"], "attendance":d["attendance"],"last_updated":datetime.now(timezone.utc) }

# 2. Convert it to a DataFrame and concat
    raw_tables_for_csv = pd.concat([raw_tables_for_csv, pd.DataFrame([new_row])], ignore_index=True)

#print(raw_tables_for_csv.head())


con = duckdb.connect("raw.db")
con.sql("CREATE TABLE IF NOT EXISTS raw_table AS SELECT * FROM raw_tables_for_csv")
print(con.sql("select * from raw_table LIMIT 3"))
con.close()