"""
Fetch all raw data needed for roster composition analysis.

Sources:
  - api-web.nhle.com/v1/roster/{team}/{season}     -> player bios (position, birthdate)
  - api.nhle.com/stats/rest/en/skater/summary      -> skater stats (TOI, points, GP)
  - api.nhle.com/stats/rest/en/goalie/summary      -> goalie stats (starts, SV%, GAA)
  - api.nhle.com/stats/rest/en/team/summary        -> team-level stats (PP%, PK%, shots)

All responses cached to data/raw/ so re-runs are fast.
"""

import json
import time
import requests
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

WEB_BASE = "https://api-web.nhle.com/v1"
STATS_BASE = "https://api.nhle.com/stats/rest/en"

SEASONS = [
    "20172018",
    "20182019",
    "20192020",
    "20202021",
    "20212022",
    "20222023",
    "20232024",
]

# All 32 franchises (31 in pre-2021 seasons — SEA joined 2021-22)
TEAMS = [
    "ANA", "ARI", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL",
    "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR",
    "OTT", "PHI", "PIT", "STL", "SJS", "SEA", "TBL", "TOR", "VAN", "VGK",
    "WSH", "WPG",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; nhl-research/1.0)"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cached_get(url: str, cache_path: Path) -> dict:
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    time.sleep(0.25)
    return data


# ---------------------------------------------------------------------------
# Roster (player bios: position, birthdate, id)
# ---------------------------------------------------------------------------

def fetch_roster(team: str, season: str) -> dict:
    cache = RAW_DIR / f"roster_{team}_{season}.json"
    url = f"{WEB_BASE}/roster/{team}/{season}"
    try:
        return cached_get(url, cache)
    except Exception as e:
        print(f"    WARN: roster {team} {season}: {e}")
        return {}


def fetch_all_rosters() -> None:
    print("Fetching rosters...")
    total = 0
    for season in SEASONS:
        season_teams = TEAMS if season >= "20212022" else [t for t in TEAMS if t != "SEA"]
        for team in season_teams:
            cache = RAW_DIR / f"roster_{team}_{season}.json"
            cached = cache.exists()
            fetch_roster(team, season)
            if not cached:
                total += 1
    print(f"  Rosters done ({total} new fetches)")


# ---------------------------------------------------------------------------
# Skater stats
# ---------------------------------------------------------------------------

def fetch_paginated(base_url: str, cache_path: Path, page_size: int = 100) -> list[dict]:
    """Fetch all pages of a paginated NHL stats REST endpoint."""
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    all_records = []
    start = 0
    while True:
        url = f"{base_url}&limit={page_size}&start={start}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", [])
        all_records.extend(records)
        if len(records) < page_size:
            break
        start += page_size
        time.sleep(0.2)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"data": all_records}, f)
    return all_records


def fetch_skater_stats(season: str) -> list[dict]:
    cache = RAW_DIR / f"skater_stats_{season}.json"
    base_url = f"{STATS_BASE}/skater/summary?cayenneExp=seasonId={season}"
    return fetch_paginated(base_url, cache)


# ---------------------------------------------------------------------------
# Goalie stats
# ---------------------------------------------------------------------------

def fetch_goalie_stats(season: str) -> list[dict]:
    cache = RAW_DIR / f"goalie_stats_{season}.json"
    base_url = f"{STATS_BASE}/goalie/summary?cayenneExp=seasonId={season}"
    return fetch_paginated(base_url, cache)


# ---------------------------------------------------------------------------
# Team stats
# ---------------------------------------------------------------------------

def fetch_team_stats(season: str) -> list[dict]:
    cache = RAW_DIR / f"team_stats_{season}.json"
    url = f"{STATS_BASE}/team/summary?cayenneExp=seasonId={season}"
    data = cached_get(url, cache)
    return data.get("data", [])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fetch_all_rosters()

    print("Fetching skater/goalie/team stats per season...")
    for season in SEASONS:
        sk = fetch_skater_stats(season)
        gl = fetch_goalie_stats(season)
        tm = fetch_team_stats(season)
        print(f"  {season}: {len(sk)} skaters, {len(gl)} goalies, {len(tm)} teams")

    print("\nDone. All data cached in data/raw/")
