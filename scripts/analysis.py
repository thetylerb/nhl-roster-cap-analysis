"""
Correlate roster construction metrics with playoff outcomes.

For each metric, computes:
  - Mean value for Cup winners vs. non-winners
  - Mean value for playoff teams vs. non-playoff teams
  - Pearson r with rounds_won
  - Whether salary hooks are populated (skips cap metrics if all null)

Outputs a ranked leaderboard and saves outputs/metric_leaderboard.png.
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from scipy import stats as scipy_stats

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# Metrics available now (roster construction)
ROSTER_METRICS = {
    "avg_age_toi":          "Avg Age (TOI-weighted)",
    "pct_under_25":         "% Roster Under 25",
    "pct_over_32":          "% Roster Over 32",
    "pts_top1_share":       "Points — Top 1 Share",
    "pts_top3_share":       "Points — Top 3 Share",
    "pts_top6_share":       "Points — Top 6 Share",
    "pts_gini":             "Points Gini (inequality)",
    "toi_gini":             "TOI Gini (inequality)",
    "toi_top4D_avg":        "Top-4 D Avg TOI/Game (min)",
    "goalie_sv_pct":        "Goalie SV%",
    "goalie_top_start_pct": "Goalie #1 Start %",
    "pp_pct":               "Power Play %",
    "pk_pct":               "Penalty Kill %",
    "shots_for_pg":         "Shots For / Game",
    "shots_against_pg":     "Shots Against / Game",
}

# Salary metrics — included only when cap data is loaded
CAP_METRICS = {
    "cap_hit_total":    "Total Cap Spend",
    "cap_pct_ceiling":  "Cap % of Ceiling",
    "cap_top1_share":   "Cap — Top 1 Share",
    "cap_top3_share":   "Cap — Top 3 Share",
    "cap_gini":         "Cap Gini (inequality)",
    "cap_fwd_pct":      "Cap — Forward %",
    "cap_def_pct":      "Cap — Defence %",
    "cap_goalie_pct":   "Cap — Goalie %",
}


def load_data() -> pd.DataFrame:
    profiles = pd.read_csv(PROCESSED_DIR / "team_profiles.csv", dtype={"season": str})
    outcomes = pd.read_csv(PROCESSED_DIR / "outcomes.csv", dtype={"season": str})
    df = profiles.merge(outcomes[["season", "team", "made_playoffs", "rounds_won", "won_cup"]],
                        on=["season", "team"], how="inner")
    return df


def analyze_metric(df: pd.DataFrame, col: str, label: str) -> dict | None:
    sub = df[[col, "rounds_won", "made_playoffs", "won_cup"]].dropna(subset=[col])
    if len(sub) < 10:
        return None

    cup = sub[sub["won_cup"] == 1][col]
    non_cup = sub[sub["won_cup"] == 0][col]
    playoff = sub[sub["made_playoffs"] == 1][col]
    non_playoff = sub[sub["made_playoffs"] == 0][col]

    r, p_val = scipy_stats.pearsonr(sub[col], sub["rounds_won"])

    return {
        "metric": col,
        "label": label,
        "r_rounds_won": r,
        "p_value": p_val,
        "cup_winner_mean": cup.mean(),
        "non_cup_mean": non_cup.mean(),
        "cup_vs_field_diff": cup.mean() - non_cup.mean(),
        "playoff_mean": playoff.mean(),
        "non_playoff_mean": non_playoff.mean(),
        "n": len(sub),
    }


def run_analysis(df: pd.DataFrame) -> pd.DataFrame:
    # Determine which metric sets to include
    all_metrics = dict(ROSTER_METRICS)
    cap_available = df["cap_hit_total"].notna().any()
    if cap_available:
        all_metrics.update(CAP_METRICS)
        print("  Cap data detected — including salary metrics.")
    else:
        print("  No cap data loaded — salary metrics skipped (hooks ready).")

    results = []
    for col, label in all_metrics.items():
        if col not in df.columns:
            continue
        result = analyze_metric(df, col, label)
        if result:
            results.append(result)

    return pd.DataFrame(results).sort_values("r_rounds_won", key=abs, ascending=False).reset_index(drop=True)


def print_leaderboard(results: pd.DataFrame) -> None:
    print(f"\n{'='*75}")
    print("  NHL ROSTER CONSTRUCTION -> PLAYOFF SUCCESS LEADERBOARD")
    print(f"{'='*75}")
    print(f"  {'Metric':<30} {'r':>7} {'p':>7}  {'Cup Avg':>8}  {'Field Avg':>9}")
    print(f"  {'-'*30} {'-'*7} {'-'*7}  {'-'*8}  {'-'*9}")
    for _, row in results.iterrows():
        sig = "*" if row["p_value"] < 0.05 else " "
        print(
            f"  {row['label']:<30} {row['r_rounds_won']:>7.3f}{sig} "
            f"{row['p_value']:>7.3f}  "
            f"{row['cup_winner_mean']:>8.3f}  {row['non_cup_mean']:>9.3f}"
        )
    print(f"{'='*75}")
    print("  * p < 0.05")

    best = results.iloc[0]
    print(f"\n  Strongest predictor: {best['label']}")
    print(f"  r = {best['r_rounds_won']:.3f} with rounds won  (p = {best['p_value']:.3f})")


def plot_top_metrics(results: pd.DataFrame, df: pd.DataFrame, top_n: int = 8) -> None:
    top = results.head(top_n)

    fig, axes = plt.subplots(2, 4, figsize=(16, 8))
    fig.suptitle("Roster Construction Metrics vs. Playoff Rounds Won", fontsize=13, fontweight="bold")
    axes = axes.flatten()

    for i, (_, row) in enumerate(top.iterrows()):
        ax = axes[i]
        col = row["metric"]
        sub = df[[col, "rounds_won", "won_cup"]].dropna(subset=[col])

        # Box plots by rounds won
        groups = [sub[sub["rounds_won"] == r][col].values for r in range(5)]
        bp = ax.boxplot([g for g in groups if len(g) > 0],
                        labels=[str(r) for r, g in enumerate(groups) if len(g) > 0],
                        patch_artist=True)
        for patch in bp["boxes"]:
            patch.set_facecolor("#aec7e8")

        ax.set_title(f"{row['label']}\nr = {row['r_rounds_won']:.3f}", fontsize=9)
        ax.set_xlabel("Rounds won", fontsize=8)
        ax.tick_params(labelsize=8)

    plt.tight_layout()
    out = OUTPUTS_DIR / "metric_leaderboard.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\n  Chart saved: {out}")


if __name__ == "__main__":
    print("Loading data...")
    df = load_data()
    print(f"  {len(df)} team-seasons ({df['season'].nunique()} seasons)")

    results = run_analysis(df)
    print_leaderboard(results)

    out_csv = PROCESSED_DIR / "metric_results.csv"
    results.to_csv(out_csv, index=False)
    print(f"  Results saved: {out_csv}")

    print("\nGenerating charts...")
    plot_top_metrics(results, df)
