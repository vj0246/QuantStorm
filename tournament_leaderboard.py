"""
tournament_leaderboard.py — Advanced Statistical Baseline Leaderboard
====================================================================
Evaluates every custom bot in the strategies folder strictly against 
the three engine baselines across multiple independent seeds.

Extracts complete empirical distributions (Mean, Variance, Std Dev, Min, Max, 
and Stability Index) to identify the highest-generalization champion.
"""

from __future__ import annotations

import sys
import statistics
from pathlib import Path
from typing import Dict, List

# Add root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from bot_loader import check_source, load_for_testing
from engine import play_match
from game_config import GameConfig

# ════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════
STRATEGIES_DIR = ROOT_DIR / "strategies"
N_DEALS_PER_SEED = 20  # 20 direct + 20 mirror = 40 deals PER seed
SEEDS = [42, 1337, 2026, 9999, 54321, 105, 953, 4045, 6666, 23400]  # 5 independent testing environments

BASELINES = ["naive_ev.py", "rational.py", "adaptive_bidder.py"]
IGNORE_FILES = {"__init__.py"}


def get_custom_strategies() -> list[Path]:
    """Retrieve all valid custom strategy files sorted by modification time."""
    files = [f for f in STRATEGIES_DIR.glob("*.py") if f.name not in IGNORE_FILES and f.name not in BASELINES]
    valid_files = [f for f in files if check_source(f).ok]
    return sorted(valid_files, key=lambda f: f.stat().st_mtime)


def compute_distribution_stats(data: List[float]) -> Dict[str, float]:
    """Computes full first- and second-order statistical moments."""
    n = len(data)
    if n == 0:
        return {"mean": 0.0, "variance": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0}
    
    return {
        "mean": statistics.mean(data),
        "variance": statistics.variance(data) if n > 1 else 0.0,
        "stdev": statistics.stdev(data) if n > 1 else 0.0,
        "min": min(data),
        "max": max(data),
    }


def evaluate_against_baseline(
    custom_bot_path: Path, baseline_name: str, config: GameConfig
) -> List[float]:
    """Run a multi-seed gauntlet against a baseline and return raw seed PnLs."""
    baseline_path = STRATEGIES_DIR / baseline_name
    if not baseline_path.exists():
        return [0.0] * len(SEEDS)

    BotCustom = load_for_testing(custom_bot_path, quiet=True)
    BotBaseline = load_for_testing(baseline_path, quiet=True)

    name_custom = getattr(BotCustom, "name", custom_bot_path.stem)
    name_baseline = getattr(BotBaseline, "name", baseline_path.stem)

    seed_pnls = []
    for seed in SEEDS:
        result = play_match(
            bot_a_factory=BotCustom,
            bot_b_factory=BotBaseline,
            config=config,
            seed=seed,
            mirror=True,
            n_deals=N_DEALS_PER_SEED,
            verbose=False,
            bot_a_name=name_custom,
            bot_b_name=name_baseline,
        )
        seed_pnls.append(result.pnl[0])

    return seed_pnls


def main():
    config = GameConfig()
    custom_bots = get_custom_strategies()

    if not custom_bots:
        print("[-] No valid custom strategies found to test.")
        return

    print("════════════════════════════════════════════════════════════════════════════════════════════")
    print(f" GLOBAL STATISTICAL TOURNAMENT LEADERBOARD")
    print(f" Seeds: {len(SEEDS)} | Baselines: 3 | Total Deals per Bot: {len(SEEDS) * len(BASELINES) * N_DEALS_PER_SEED * 2}")
    print("════════════════════════════════════════════════════════════════════════════════════════════\n")

    leaderboard = []

    for bot_path in custom_bots:
        bot_name = bot_path.name
        print(f"[*] Evaluating {bot_name:<20s} ... ", end="", flush=True)
        
        bot_baseline_stats = {}
        all_seed_pnls = []
        total_ev = 0.0

        for baseline in BASELINES:
            pnls = evaluate_against_baseline(bot_path, baseline, config)
            stats = compute_distribution_stats(pnls)
            
            clean_base_name = baseline.replace(".py", "")
            bot_baseline_stats[clean_base_name] = stats
            all_seed_pnls.extend(pnls)
            total_ev += stats["mean"]

        global_stats = compute_distribution_stats(all_seed_pnls)
        stability_score = total_ev / global_stats["stdev"] if global_stats["stdev"] > 0 else 0.0

        leaderboard.append({
            "bot_name": bot_name,
            "total_ev": total_ev,
            "global_mean": global_stats["mean"],
            "global_var": global_stats["variance"],
            "global_std": global_stats["stdev"],
            "global_min": global_stats["min"],
            "global_max": global_stats["max"],
            "stability": stability_score,
            "baselines": bot_baseline_stats,
        })
        print(f"Done. (Total EV: {total_ev:+.2f})")

    # Sort descending by Total Expected Value
    leaderboard.sort(key=lambda x: x["total_ev"], reverse=True)

    # ════════════════════════════════════════════════════════════════════
    # CONCISE & READABLE SUMMARY TABLE
    # ════════════════════════════════════════════════════════════════════
    print("\n\nFINAL RANKINGS (Sorted by Total Expected Value)")
    print("┌──────┬──────────────────────┬──────────┬───────────┬──────────────┬──────────────┬──────────────┐")
    print("│ Rank │ Bot Name             │ Total EV │ Stability │ vs NaiveEV   │ vs Rational  │ vs Adaptive  │")
    print("├──────┼──────────────────────┼──────────┼───────────┼──────────────┼──────────────┼──────────────┤")

    for i, entry in enumerate(leaderboard, 1):
        b = entry["baselines"]
        
        # Format strings as "Mean ± StdDev"
        naive_str = f"{b['naive_ev']['mean']:+6.1f} ±{b['naive_ev']['stdev']:4.1f}"
        rat_str = f"{b['rational']['mean']:+6.1f} ±{b['rational']['stdev']:4.1f}"
        adapt_str = f"{b['adaptive_bidder']['mean']:+6.1f} ±{b['adaptive_bidder']['stdev']:4.1f}"

        print(
            f"│ #{i:<3d} │ {entry['bot_name']:<20s} │ {entry['total_ev']:>+8.2f} │ {entry['stability']:>9.2f} │ {naive_str:>12s} │ {rat_str:>12s} │ {adapt_str:>12s} │"
        )

    print("└──────┴──────────────────────┴──────────┴───────────┴──────────────┴──────────────┴──────────────┘")

    best = leaderboard[0]
    print(f"\n🏆 TOURNAMENT CHAMPION: {best['bot_name']}")
    print(f"   • Total Expected Value : {best['total_ev']:+.2f}")
    print(f"   • Stability Score      : {best['stability']:.2f}")
    print(f"   • Extreme Seed Bounds  : [{best['global_min']:+.2f}, {best['global_max']:+.2f}]\n")


if __name__ == "__main__":
    main()