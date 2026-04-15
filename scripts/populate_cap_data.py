"""
Populate salary cap columns in team_profiles.csv from cached capwages data.

Reads:
  data/raw/roster_*.json            — team rosters per season (from NHL API)
  data/raw/cap_player_*.json        — player contract data (from capwages, via fetch_cap_data.py)

Fills in the nine null cap columns in team_profiles.csv:
  cap_hit_total     total roster cap spend for the season
  cap_pct_ceiling   total as % of that season's salary cap ceiling
  cap_hit_top1      cap hit of the highest-paid player
  cap_top1_share    top player cap hit / total
  cap_top3_share    top 3 players cap hit / total
  cap_gini          Gini coefficient of individual cap hits
  cap_fwd_pct       % of cap going to forwards
  cap_def_pct       % of cap going to defensemen
  cap_goalie_pct    % of cap going to goalies
"""

import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

SEASONS = [
    "20172018",
    "20182019",
    "20192020",
    "20202021",
    "20212022",
    "20222023",
    "20232024",
]

# Official NHL salary cap ceilings per season
CAP_CEILING = {
    "20172018": 75_000_000,
    "20182019": 79_500_000,
    "20192020": 81_500_000,
    "20202021": 81_500_000,
    "20212022": 81_500_000,
    "20222023": 82_500_000,
    "20232024": 83_500_000,
}

# Convert our "20232024" season ID to capwages "2023-24" format
def season_to_label(season: str) -> str:
    return f"{season[:4][-2:]}-{season[4:][-2:]}"  # "20232024" -> "23-24"


def season_to_capwages(season: str) -> str:
    """20232024 -> 2023-24"""
    yr_start = season[:4]
    yr_end = season[4:]
    return f"{yr_start}-{yr_end[2:]}"


# ---------------------------------------------------------------------------
# Slug helpers (same logic as fetch_cap_data.py)
# ---------------------------------------------------------------------------

def make_slug(first: str, last: str) -> str:
    name = f"{first} {last}".lower()
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9-]", "", ascii_name.replace(" ", "-"))


# ---------------------------------------------------------------------------
# Contract helpers
# ---------------------------------------------------------------------------

def parse_dollar(val: str | None) -> float | None:
    if not val:
        return None
    try:
        return float(val.replace("$", "").replace(",", ""))
    except ValueError:
        return None


def get_cap_hit(player_data: dict, target_season: str) -> float | None:
    """
    Return cap hit (float) for target_season (e.g. "2023-24") from a player's
    capwages data dict. Returns None if no matching contract detail found.
    """
    for contract in player_data.get("contracts", []):
        for detail in contract.get("details", []):
            if detail.get("season") == target_season:
                return parse_dollar(detail.get("capHit"))
    return None


def gini(values: list[float]) -> float:
    if not values or sum(values) == 0:
        return float("nan")
    arr = sorted(values)
    n = len(arr)
    cumulative = sum((i + 1) * v for i, v in enumerate(arr))
    return (2 * cumulative) / (n * sum(arr)) - (n + 1) / n


# ---------------------------------------------------------------------------
# Load all cached player cap data
# ---------------------------------------------------------------------------

def load_cap_cache() -> dict[str, dict]:
    """Returns {slug: player_data} for all successfully cached players."""
    cache: dict[str, dict] = {}
    for path in RAW_DIR.glob("cap_player_*.json"):
        slug = path.stem.removeprefix("cap_player_")
        try:
            with open(path, encoding="utf-8") as f:
                cache[slug] = json.load(f)
        except Exception:
            pass
    return cache


# ---------------------------------------------------------------------------
# Build cap profile for one team-season
# ---------------------------------------------------------------------------

def build_cap_profile(
    team: str,
    season: str,
    cap_cache: dict[str, dict],
) -> dict:
    """
    Returns a dict with all cap_ columns for this team-season.
    All values will be None if insufficient data.
    """
    empty = {
        "cap_hit_total": None, "cap_pct_ceiling": None,
        "cap_hit_top1": None, "cap_top1_share": None, "cap_top3_share": None,
        "cap_gini": None,
        "cap_fwd_pct": None, "cap_def_pct": None, "cap_goalie_pct": None,
    }

    roster_path = RAW_DIR / f"roster_{team}_{season}.json"
    if not roster_path.exists():
        return empty

    with open(roster_path, encoding="utf-8") as f:
        roster = json.load(f)

    cap_season = season_to_capwages(season)  # "2023-24"
    ceiling = CAP_CEILING.get(season)

    hits_by_pos: dict[str, list[float]] = {"F": [], "D": [], "G": []}

    for group, pos_label in [("forwards", "F"), ("defensemen", "D"), ("goalies", "G")]:
        for p in roster.get(group, []):
            first = p.get("firstName", {}).get("default", "")
            last = p.get("lastName", {}).get("default", "")
            if not first or not last:
                continue
            slug = make_slug(first, last)
            player_data = cap_cache.get(slug)
            if player_data is None:
                continue
            hit = get_cap_hit(player_data, cap_season)
            if hit and hit > 0:
                hits_by_pos[pos_label].append(hit)

    all_hits = hits_by_pos["F"] + hits_by_pos["D"] + hits_by_pos["G"]
    if len(all_hits) < 5:
        return empty  # too few players found to be meaningful

    all_hits_sorted = sorted(all_hits, reverse=True)
    total = sum(all_hits)

    result = {
        "cap_hit_total": total,
        "cap_pct_ceiling": total / ceiling if ceiling else None,
        "cap_hit_top1": all_hits_sorted[0],
        "cap_top1_share": all_hits_sorted[0] / total,
        "cap_top3_share": sum(all_hits_sorted[:3]) / total if len(all_hits_sorted) >= 3 else None,
        "cap_gini": gini(all_hits),
        "cap_fwd_pct": sum(hits_by_pos["F"]) / total if total else None,
        "cap_def_pct": sum(hits_by_pos["D"]) / total if total else None,
        "cap_goalie_pct": sum(hits_by_pos["G"]) / total if total else None,
    }
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Loading cap cache...")
    cap_cache = load_cap_cache()
    print(f"  {len(cap_cache)} players in cap cache")

    print("Loading team_profiles.csv...")
    profiles_path = PROCESSED_DIR / "team_profiles.csv"
    df = pd.read_csv(profiles_path, dtype={"season": str})
    print(f"  {len(df)} rows")

    teams_updated = 0
    teams_no_data = 0

    for idx, row in df.iterrows():
        season = row["season"]
        team = row["team"]

        cap = build_cap_profile(team, season, cap_cache)

        has_data = cap["cap_hit_total"] is not None
        if has_data:
            teams_updated += 1
        else:
            teams_no_data += 1

        for col, val in cap.items():
            df.at[idx, col] = val

    print(f"  Updated: {teams_updated} rows with cap data")
    print(f"  No data: {teams_no_data} rows (insufficient player matches)")

    df.to_csv(profiles_path, index=False)
    print(f"\nSaved: {profiles_path}")

    # Quick sanity check
    filled = df["cap_hit_total"].notna().sum()
    total = len(df)
    print(f"Coverage: {filled}/{total} team-seasons have cap data ({100*filled/total:.0f}%)")

    if filled > 0:
        sample = df[df["cap_hit_total"].notna()][["season", "team", "cap_hit_total", "cap_pct_ceiling", "cap_gini", "cap_fwd_pct"]].head(10)
        print("\nSample:")
        print(sample.to_string(index=False))
