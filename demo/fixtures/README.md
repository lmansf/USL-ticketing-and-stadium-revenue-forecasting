# Fixtures

Saved payloads, so no test and no demo depends on a live API call or a working
subscription.

| File | Purpose |
|---|---|
| `league-matches_season_id_1625_drifted.json` | The archived EPL 2018/19 response cut to its first five match records, with `homeID` and `awayID` deleted from every record. Same top-level keys as the real response (`success`, `pager`, `metadata`, `data`, `message`), pretty-printed. Feeds `show_schema_drift.py`, which expects `SchemaDriftError` naming both missing fields |

Most fixtures do not live here. The real ones are in `data/raw_archive/`, which is
committed for exactly this reason - a response pulled with the free `example` key is a
permanent test fixture that costs nothing and keeps working after the subscription ends.

This directory holds only *deliberately corrupted* payloads, which do not belong in the
archive because the archive is meant to be a faithful record of what the API actually
said.

To regenerate the drifted fixture from the archive:

```python
import json
from pathlib import Path

payload = json.loads(Path("data/raw_archive/league-matches_season_id_1625.json").read_text())
for record in payload["data"][:5]:
    del record["homeID"], record["awayID"]
payload["data"] = payload["data"][:5]
Path("demo/fixtures/league-matches_season_id_1625_drifted.json").write_text(
    json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
)
```
