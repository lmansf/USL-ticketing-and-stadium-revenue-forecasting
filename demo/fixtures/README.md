# Fixtures

Saved payloads, so no test and no demo depends on a live API call or a working
subscription.

| File | Purpose |
|---|---|
| `league-matches_season_1625_drifted.json` | An archived response with a required field removed. Feeds `show_schema_drift.py` |

Most fixtures do not live here. The real ones are in `data/raw_archive/`, which is
committed for exactly this reason - a response pulled with the free `example` key is a
permanent test fixture that costs nothing and keeps working after the subscription ends.

This directory holds only *deliberately corrupted* payloads, which do not belong in the
archive because the archive is meant to be a faithful record of what the API actually
said.

Both are TODO - create them once you have archived a real response.
