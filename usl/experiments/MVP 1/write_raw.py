import requests
from pathlib import Path
import json
# season_id, not league_id - 1625 is a season id. The archived file is named the
# way usl/ingest/archive.py names it, so the package serves it as an archive hit.
URL = "https://api.football-data-api.com/league-matches?key=example&season_id=1625"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://google.com"
}
ARCHIVE_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_archive"
season_id = 1625
path = ARCHIVE_DIR / f"league-matches_season_id_{season_id}.json"
if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
else:
    body = requests.get(URL, headers=headers, timeout=30).text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")   # durable BEFORE anything can fail
    payload = json.loads(body)
