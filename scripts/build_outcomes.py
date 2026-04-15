"""
Pull playoff outcomes from the nhl_data project and build a clean
outcomes table for joining with team profiles.

Output columns:
  season, team, made_playoffs, rounds_won, won_cup, league_rank, conf_rank
"""

import pandas as pd
from pathlib import Path

# Path to the sibling project's processed data
NHL_DATA_DIR = Path(__file__).parent.parent.parent / "nhl_data" / "data" / "processed"

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def build_outcomes() -> pd.DataFrame:
    matchups_path = NHL_DATA_DIR / "playoff_matchups.csv"
    standings_path = NHL_DATA_DIR / "standings_all.csv"

    if not matchups_path.exists():
        raise FileNotFoundError(
            f"Could not find playoff_matchups.csv at {matchups_path}\n"
            "Make sure the nhl_data project has been run first."
        )

    matchups = pd.read_csv(matchups_path, dtype={"season": str})
    standings = pd.read_csv(standings_path, dtype={"season": str})

    # All teams that appeared in any playoff series
    playoff_teams = pd.concat([
        matchups[["season", "team_hi"]].rename(columns={"team_hi": "team"}),
        matchups[["season", "team_lo"]].rename(columns={"team_lo": "team"}),
    ]).drop_duplicates()

    # Rounds won per team per season
    rounds_won = (
        matchups.groupby(["season", "winner"])
        .size()
        .reset_index(name="rounds_won")
        .rename(columns={"winner": "team"})
    )

    # Cup winners
    cup_winners = (
        matchups[matchups["round"] == 4][["season", "winner"]]
        .rename(columns={"winner": "team"})
        .assign(won_cup=1)
    )

    # All 32 teams each season — mark playoff vs not
    all_teams = standings[["season", "team"]].copy()

    # League rank from standings
    standings_sorted = standings.sort_values(
        ["season", "points", "wins", "goal_diff"], ascending=[True, False, False, False]
    )
    standings_sorted["league_rank"] = standings_sorted.groupby("season").cumcount() + 1

    all_teams = all_teams.merge(
        standings_sorted[["season", "team", "league_rank"]], on=["season", "team"], how="left"
    )

    all_teams["made_playoffs"] = all_teams.set_index(["season", "team"]).index.isin(
        playoff_teams.set_index(["season", "team"]).index
    ).astype(int)

    all_teams = all_teams.merge(rounds_won, on=["season", "team"], how="left")
    all_teams["rounds_won"] = all_teams["rounds_won"].fillna(0).astype(int)

    all_teams = all_teams.merge(cup_winners, on=["season", "team"], how="left")
    all_teams["won_cup"] = all_teams["won_cup"].fillna(0).astype(int)

    return all_teams


if __name__ == "__main__":
    df = build_outcomes()
    out = PROCESSED_DIR / "outcomes.csv"
    df.to_csv(out, index=False)
    print(f"Saved: {out}  ({len(df)} rows)")
    print(f"  Playoff teams: {df['made_playoffs'].sum()}")
    print(f"  Cup winners:   {df['won_cup'].sum()}")
    print(df[df["won_cup"] == 1][["season", "team", "league_rank", "rounds_won"]].to_string(index=False))
