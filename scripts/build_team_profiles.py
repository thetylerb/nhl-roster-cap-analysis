"""
Build one row per team per season with roster composition metrics.

Roster construction metrics (from API data):
  Age profile:
    avg_age_toi       - TOI-weighted average age of skaters
    pct_under_25      - % of skater roster GP under age 25
    pct_over_32       - % of skater roster GP over age 32

  Scoring distribution (proxy for cap concentration until salary data available):
    pts_top1_share    - % of team points scored by the top scorer
    pts_top3_share    - % by top 3 scorers
    pts_top6_share    - % by top 6 scorers
    pts_gini          - Gini coefficient of individual point totals

  Ice time concentration:
    toi_gini          - Gini coefficient of skater TOI
    toi_top4D_avg     - average TOI of top 4 D (defensive investment)

  Goalie:
    goalie_sv_pct     - team save percentage
    goalie_top_start_pct - % of starts by the #1 goalie

  Special teams (from team stats API):
    pp_pct            - power play percentage
    pk_pct            - penalty kill percentage
    shots_for_pg      - shots for per game
    shots_against_pg  - shots against per game

  --- SALARY HOOKS (null until cap data is loaded) ---
    cap_hit_total     - total cap spend
    cap_pct_ceiling   - cap spend as % of salary cap ceiling
    cap_hit_top1      - cap hit of highest-paid player
    cap_top1_share    - top player cap hit as % of total
    cap_top3_share    - top 3 as % of total
    cap_gini          - Gini coefficient of cap hit distribution
    cap_fwd_pct       - % of cap to forwards
    cap_def_pct       - % of cap to defensemen
    cap_goalie_pct    - % of cap to goalies
"""

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

SEASONS = [
    "20172018",
    "20182019",
    "20192020",
    "20202021",
    "20212022",
    "20222023",
    "20232024",
]

# Approximate start of each regular season (used for age calculation)
SEASON_START = {
    "20172018": date(2017, 10, 4),
    "20182019": date(2018, 10, 3),
    "20192020": date(2019, 10, 2),
    "20202021": date(2021, 1, 13),
    "20212022": date(2021, 10, 12),
    "20222023": date(2022, 10, 7),
    "20232024": date(2023, 10, 10),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def gini(values: list[float]) -> float:
    """Gini coefficient of a list of non-negative values. 0=equal, 1=max inequality."""
    if not values or sum(values) == 0:
        return float("nan")
    arr = sorted(values)
    n = len(arr)
    cumulative = sum((i + 1) * v for i, v in enumerate(arr))
    return (2 * cumulative) / (n * sum(arr)) - (n + 1) / n


def age_on(birthdate_str: str, ref_date: date) -> float | None:
    """Calculate age in decimal years on ref_date."""
    try:
        bd = date.fromisoformat(birthdate_str)
        days = (ref_date - bd).days
        return days / 365.25
    except Exception:
        return None


def toi_to_seconds(toi_str) -> float:
    """Convert 'MM:SS' TOI string to seconds. Handles numeric seconds too."""
    if toi_str is None:
        return 0.0
    if isinstance(toi_str, (int, float)):
        return float(toi_str)
    try:
        parts = str(toi_str).split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0.0


def load_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Per-season builders
# ---------------------------------------------------------------------------

def get_team_abbrevs(season: str) -> list[str]:
    """Return the list of teams active in a given season."""
    from scripts.fetch_data import TEAMS
    if season < "20212022":
        return [t for t in TEAMS if t != "SEA"]
    return TEAMS


def build_roster_lookup(season: str) -> dict[str, dict]:
    """
    Returns {player_id: {birthdate, position_group}} for all players
    rostered by any team in the season.
    position_group: F / D / G
    """
    lookup = {}
    for path in RAW_DIR.glob(f"roster_*_{season}.json"):
        data = load_json(path)
        for group, pos_label in [("forwards", "F"), ("defensemen", "D"), ("goalies", "G")]:
            for p in data.get(group, []):
                pid = p.get("id")
                if pid:
                    lookup[pid] = {
                        "birthdate": p.get("birthDate", ""),
                        "position_group": pos_label,
                    }
    return lookup


def build_season_profiles(season: str) -> list[dict]:
    ref_date = SEASON_START[season]
    roster_lookup = build_roster_lookup(season)

    # Load stats
    skater_data = load_json(RAW_DIR / f"skater_stats_{season}.json").get("data", [])
    goalie_data = load_json(RAW_DIR / f"goalie_stats_{season}.json").get("data", [])
    team_data   = load_json(RAW_DIR / f"team_stats_{season}.json").get("data", [])

    # Index team stats by abbreviation
    team_stats = {}
    for t in team_data:
        abbrev = t.get("teamAbbrevs") or ""
        if not abbrev and t.get("teamFullName"):
            abbrev = t["teamFullName"][:3].upper()
        team_stats[abbrev] = t

    # Group skaters and goalies by their (last) team
    skaters_by_team: dict[str, list] = {}
    for sk in skater_data:
        # teamAbbrevs may be comma-separated for traded players — use last team
        team = sk.get("teamAbbrevs", "").split(",")[-1].strip()
        if team:
            skaters_by_team.setdefault(team, []).append(sk)

    goalies_by_team: dict[str, list] = {}
    for gl in goalie_data:
        team = gl.get("teamAbbrevs", "").split(",")[-1].strip()
        if team:
            goalies_by_team.setdefault(team, []).append(gl)

    profiles = []
    teams = get_team_abbrevs(season)

    for team in teams:
        skaters = skaters_by_team.get(team, [])
        goalies = goalies_by_team.get(team, [])
        ts = team_stats.get(team, {})

        if not skaters and not ts:
            continue  # no data for this team this season

        row = {"season": season, "team": team}

        # ---- Age profile -----------------------------------------------
        ages, toi_secs, gp_counts = [], [], []
        for sk in skaters:
            pid = sk.get("playerId")
            bio = roster_lookup.get(pid, {})
            age = age_on(bio.get("birthdate", ""), ref_date)
            toi = toi_to_seconds(sk.get("timeOnIcePerGame")) * sk.get("gamesPlayed", 0)
            gp  = sk.get("gamesPlayed", 0)
            if age and toi > 0:
                ages.append((age, toi))
            if age and gp > 0:
                gp_counts.append((age, gp))

        if ages:
            total_toi = sum(t for _, t in ages)
            row["avg_age_toi"] = sum(a * t for a, t in ages) / total_toi if total_toi else None
        else:
            row["avg_age_toi"] = None

        if gp_counts:
            total_gp = sum(g for _, g in gp_counts)
            row["pct_under_25"] = sum(g for a, g in gp_counts if a < 25) / total_gp
            row["pct_over_32"]  = sum(g for a, g in gp_counts if a > 32) / total_gp
        else:
            row["pct_under_25"] = None
            row["pct_over_32"]  = None

        # ---- Scoring distribution --------------------------------------
        points = sorted(
            [sk.get("points", 0) or 0 for sk in skaters], reverse=True
        )
        total_pts = sum(points)
        if total_pts > 0:
            row["pts_top1_share"] = points[0] / total_pts if points else None
            row["pts_top3_share"] = sum(points[:3]) / total_pts if len(points) >= 3 else None
            row["pts_top6_share"] = sum(points[:6]) / total_pts if len(points) >= 6 else None
            row["pts_gini"]       = gini(points)
        else:
            row["pts_top1_share"] = row["pts_top3_share"] = row["pts_top6_share"] = row["pts_gini"] = None

        # ---- TOI concentration -----------------------------------------
        all_toi = [
            toi_to_seconds(sk.get("timeOnIcePerGame")) * (sk.get("gamesPlayed") or 0)
            for sk in skaters
        ]
        row["toi_gini"] = gini([t for t in all_toi if t > 0])

        # Top-4 D average TOI/game
        d_skaters = [
            sk for sk in skaters
            if roster_lookup.get(sk.get("playerId"), {}).get("position_group") == "D"
        ]
        d_toi_pg = sorted(
            [toi_to_seconds(sk.get("timeOnIcePerGame")) for sk in d_skaters],
            reverse=True
        )
        row["toi_top4D_avg"] = np.mean(d_toi_pg[:4]) / 60 if len(d_toi_pg) >= 4 else None

        # ---- Goalie ----------------------------------------------------
        if goalies:
            starts = [(gl.get("gamesStarted") or 0, gl.get("savePct") or 0) for gl in goalies]
            total_starts = sum(s for s, _ in starts)
            top_starts = max(s for s, _ in starts) if starts else 0
            row["goalie_top_start_pct"] = top_starts / total_starts if total_starts else None

        else:
            row["goalie_top_start_pct"] = None

        row["goalie_sv_pct"] = ts.get("savePct") if ts else None

        # ---- Special teams / team stats --------------------------------
        row["pp_pct"]           = ts.get("powerPlayPct")
        row["pk_pct"]           = ts.get("penaltyKillPct")
        row["shots_for_pg"]     = ts.get("shotsForPerGame")
        row["shots_against_pg"] = ts.get("shotsAgainstPerGame")
        row["team_points"]      = ts.get("points")
        row["games_played"]     = ts.get("gamesPlayed")

        # ---- Salary hooks (null — ready for cap data) ------------------
        row["cap_hit_total"]    = None
        row["cap_pct_ceiling"]  = None
        row["cap_hit_top1"]     = None
        row["cap_top1_share"]   = None
        row["cap_top3_share"]   = None
        row["cap_gini"]         = None
        row["cap_fwd_pct"]      = None
        row["cap_def_pct"]      = None
        row["cap_goalie_pct"]   = None

        profiles.append(row)

    return profiles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    all_profiles = []
    for season in SEASONS:
        profiles = build_season_profiles(season)
        all_profiles.extend(profiles)
        print(f"  {season}: {len(profiles)} team profiles built")

    df = pd.DataFrame(all_profiles)
    out = PROCESSED_DIR / "team_profiles.csv"
    df.to_csv(out, index=False)
    print(f"\nSaved: {out}  ({len(df)} rows)")
    print(df[["season", "team", "avg_age_toi", "pts_top1_share", "toi_gini", "pp_pct"]].head(10).to_string(index=False))
