"""
Fetch salary cap data from capwages.com for all players in our NHL roster data.

Reads data/raw/roster_*.json to identify all players across all team-seasons,
then fetches each player's capwages.com page and caches the contract data.

Outputs:
  data/raw/cap_player_{slug}.json  — per-player contract history from capwages
  data/raw/cap_player_{slug}_404.txt  — sentinel for players not found (skip on re-run)
"""

import json
import re
import time
import unicodedata
from pathlib import Path

import requests

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; nhl-research/1.0)"}
CAPWAGES_BASE = "https://www.capwages.com/players"


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def make_slug(first: str, last: str) -> str:
    """Convert a player's first and last name to a capwages.com URL slug."""
    name = f"{first} {last}".lower()
    # Decompose unicode accents (é -> e, ü -> u, etc.)
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Replace spaces with hyphens, remove anything not alphanumeric or hyphen
    slug = re.sub(r"[^a-z0-9-]", "", ascii_name.replace(" ", "-"))
    return slug


# ---------------------------------------------------------------------------
# Roster scan
# ---------------------------------------------------------------------------

def collect_all_players() -> dict[str, dict]:
    """
    Scan all cached roster files and return {slug: {first, last, position_group}}
    for every unique player seen across all team-seasons.
    """
    players: dict[str, dict] = {}
    for roster_path in sorted(RAW_DIR.glob("roster_*.json")):
        try:
            with open(roster_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        for group, pos_label in [("forwards", "F"), ("defensemen", "D"), ("goalies", "G")]:
            for p in data.get(group, []):
                first = p.get("firstName", {}).get("default", "")
                last = p.get("lastName", {}).get("default", "")
                if not first or not last:
                    continue
                slug = make_slug(first, last)
                if slug and slug not in players:
                    players[slug] = {
                        "first": first,
                        "last": last,
                        "position_group": pos_label,
                        "slug": slug,
                    }

    return players


# ---------------------------------------------------------------------------
# Fetch a single player
# ---------------------------------------------------------------------------

def fetch_player(slug: str) -> dict | None:
    """
    Fetch capwages.com/players/{slug} and extract contract data.
    Returns the player dict (with .contracts list) or None if not found.
    """
    cache_ok = RAW_DIR / f"cap_player_{slug}.json"
    cache_miss = RAW_DIR / f"cap_player_{slug}_404.txt"

    if cache_ok.exists():
        with open(cache_ok, encoding="utf-8") as f:
            return json.load(f)
    if cache_miss.exists():
        return None

    url = f"{CAPWAGES_BASE}/{slug}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except Exception as e:
        print(f"    ERROR fetching {slug}: {e}")
        return None

    if resp.status_code == 404:
        cache_miss.write_text("404", encoding="utf-8")
        return None

    if resp.status_code != 200:
        print(f"    WARN {slug}: HTTP {resp.status_code}")
        return None

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        resp.text, re.DOTALL,
    )
    if not match:
        print(f"    WARN {slug}: no __NEXT_DATA__")
        return None

    try:
        page_data = json.loads(match.group(1))
        player = page_data["props"]["pageProps"]["player"]
    except (KeyError, json.JSONDecodeError) as e:
        print(f"    WARN {slug}: parse error: {e}")
        return None

    with open(cache_ok, "w", encoding="utf-8") as f:
        json.dump(player, f)

    return player


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_dollar(val: str | None) -> float | None:
    """'$8,250,000' -> 8250000.0. Returns None on failure."""
    if not val:
        return None
    try:
        return float(val.replace("$", "").replace(",", ""))
    except ValueError:
        return None


if __name__ == "__main__":
    print("Scanning roster files for unique players...")
    all_players = collect_all_players()
    print(f"  Found {len(all_players)} unique players across all team-seasons")

    # Partition into already-cached vs. still-needed
    to_fetch = [
        info for slug, info in all_players.items()
        if not (RAW_DIR / f"cap_player_{slug}.json").exists()
        and not (RAW_DIR / f"cap_player_{slug}_404.txt").exists()
    ]
    already_done = len(all_players) - len(to_fetch)
    print(f"  {already_done} already cached, {len(to_fetch)} to fetch")

    ok = 0
    miss = 0
    for i, info in enumerate(to_fetch, 1):
        slug = info["slug"]
        player = fetch_player(slug)
        if player:
            ok += 1
        else:
            miss += 1

        if i % 50 == 0 or i == len(to_fetch):
            print(f"  [{i}/{len(to_fetch)}] found={ok} not_found={miss}")

        time.sleep(0.4)  # polite rate limit

    print(f"\nDone. {ok} players fetched, {miss} not on capwages.")
    print(f"Cache files in {RAW_DIR}/")
