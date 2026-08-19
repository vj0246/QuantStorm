"""
evaluate_bots.py — In-Memory Deep Trace Game Inspector
======================================================
Inspects the game flow turn-by-turn between two bots by wrapping their 
decision methods in memory after validating through bot_loader.
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path

# Add root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from bot_loader import load_for_testing
from engine import play_match
from game_config import GameConfig


def make_traced_bot(base_cls, label: str):
    """Dynamically wraps a bot class in memory with turn-by-turn telemetry."""
    class TracedBot:
        name = getattr(base_cls, "name", label)

        def __init__(self):
            self._bot = base_cls()

        def reset(self, seat: int, config, seed: int) -> None:
            self.seat = seat
            print(f"\n{'='*75}\n[DEAL START] {label} ({self.name}) | Seat: {seat} | Seed: {seed}\n{'='*75}")
            self._bot.reset(seat, config, seed)

        def bid(self, obs, offered: list) -> dict:
            res = self._bot.bid(obs, offered)
            print(f"  • [{label} | Round {obs.round}] BID     -> Offered: {offered} | TE: {obs.te_mine} | Bids: {res}")
            return res

        def quote(self, obs) -> tuple:
            res = self._bot.quote(obs)
            spread = res[1] - res[0]
            print(f"  • [{label} | Round {obs.round} | Turn {obs.turn}] QUOTE   -> [{res[0]}, {res[1]}] (Spread: {spread}) | k_mine: {obs.k_mine}")
            return res

        def respond(self, obs, quote: tuple, turn: int):
            res = self._bot.respond(obs, quote, turn)
            opp_spread = quote[1] - quote[0]
            print(f"  • [{label} | Round {obs.round} | Turn {turn}] RESPOND -> vs Quote [{quote[0]}, {quote[1]}] (Spread: {opp_spread}) | Action: {res}")
            return res

        def use_transform(self, obs) -> bool:
            res = self._bot.use_transform(obs)
            print(f"  • [{label} | Round {obs.round}] TRANSFORM -> Decision: {res} | k_mine: {obs.k_mine}")
            return res

    return TracedBot


def main():
    parser = argparse.ArgumentParser(description="Deep-trace game inspector for two QuantStorm bots.")
    parser.add_argument("bot1", type=str, help="Path to Bot 1 (.py file)")
    parser.add_argument("bot2", type=str, help="Path to Bot 2 (.py file)")
    parser.add_argument("--seed", type=int, default=42, help="Match random seed (default: 42)")
    parser.add_argument("--deals", type=int, default=1, help="Number of deals to play (default: 1)")
    args = parser.parse_args()

    bot1_path = Path(args.bot1)
    bot2_path = Path(args.bot2)

    if not bot1_path.exists() or not bot2_path.exists():
        print(f"[-] Error: Could not find files: {bot1_path} or {bot2_path}")
        sys.exit(1)

    print(f"[*] Validating and loading {bot1_path.name}...")
    Bot1_Base = load_for_testing(bot1_path, quiet=False)
    print(f"[*] Validating and loading {bot2_path.name}...")
    Bot2_Base = load_for_testing(bot2_path, quiet=False)

    # Wrap classes with telemetry in memory
    TracedBot1 = make_traced_bot(Bot1_Base, "BOT_1")
    TracedBot2 = make_traced_bot(Bot2_Base, "BOT_2")

    config = GameConfig()
    print(f"\n[*] Executing {args.deals} Deal Match (Seed: {args.seed})...\n")

    result = play_match(
        bot_a_factory=TracedBot1,
        bot_b_factory=TracedBot2,
        config=config,
        seed=args.seed,
        mirror=False,
        n_deals=args.deals,
        verbose=True,
        bot_a_name=f"Bot1 ({bot1_path.stem})",
        bot_b_name=f"Bot2 ({bot2_path.stem})",
    )

    print(f"\n{'='*75}")
    print(f" MATCH SUMMARY")
    print(f"{'='*75}")
    print(f" Bot 1 ({bot1_path.stem:<15s}) PnL: {result.pnl[0]:+8.2f}")
    print(f" Bot 2 ({bot2_path.stem:<15s}) PnL: {result.pnl[1]:+8.2f}")
    print(f"{'='*75}\n")


if __name__ == "__main__":
    main()