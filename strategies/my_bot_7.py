# Name: Vivaan Jain
# College: Dwarkadas Jivanlal College of Engineering 
# Roll Number: 60004240203

"""
starter_bot.py — Divided Oracle: QuantStorm 2026, my_bot_2
======================== ====================================

STATUS: beats every provided baseline, and beats my_bot_1 (the previous
delivered version — internally named DividedOracleV1).

  vs my_bot_1 (v1)     +0.78/deal   (n=800, seed=71)
  vs NaiveEV           +11.01/deal  (n=300, seed=3)
  vs Rational          +10.66/deal  (n=300, seed=3)
  vs AdaptiveBidder    +4.25/deal   (n=300, seed=3)   <- strongest reference
`--isolate` gives an identical score to non-isolate (seed=99) — confirmed
again on this version, no accidental cross-deal state.

Re-run yourself before trusting any of this on a different seed:
    python backtester.py --bot1 strategies/my_bot.py --bot2 strategies/adaptive_bidder.py --n_deals 300 --seed <n>

CORRECTION ON WHAT THIS IS BUILT FROM
--------------------------------------
There is no "V5" in this thread and no evaluate_bots.py in this repo (the
harness is backtester.py — confirmed from the actual zip, ls'd directly).
This file is built on exactly one prior version — my_bot_1 — plus two new,
individually isolated-and-measured changes below. Nothing is "restored"
from anywhere unseen; both changes were written and backtested this
session. If real V5 code exists somewhere, paste it and I'll diff it
properly instead of working from a description.

Two ideas from that description WERE real and worth doing, so I built and
tested them for real rather than taking the claim on faith:

CHANGE 1 — counter width floors at final_cap, not 0 (HIGH confidence)
------------------------------------------------------------------------
my_bot_1's counter width was `max(0, spread - MIN_REDUCTION)`, which lets
successive counters shrink the range past the point the game's own math
treats as meaningful — engine.py's own sanitiser comment says convergence
is meant to stop at `obs.final_cap`, not zero. Fixed: `max(obs.final_cap,
spread - MIN_REDUCTION)`. Isolated test, two seeds, 300 deals each:
+0.64/deal and +0.44/deal over my_bot_1. Small, consistently positive,
zero theoretical downside (it only removes wasted WIDTH_PREMIUM) — this
one I'd ship on the logic alone even without the backtest.

CHANGE 2 — deliberate forcing on the last turn (MEDIUM confidence)
------------------------------------------------------------------------
Turn parity means the TAKER always speaks last (turns 2,4,6 are Taker's;
N_TURNS=6). MIDPOINT_SIDE_RULE="last_quoter_sells" (game_config.py L180),
so countering instead of accepting on that final turn makes YOU the SHORT
side of a forced fill at (nb+na)//2 + shift — and the sanitiser only
caps width from above, so nb=na=ask is a legal counter, pinning the
forced price to the top of the live range. Net payoff vs the best
available accept:
    force_payoff  = (ask - v) + shift_mine - FORCED_FILL_FEE(2.0)
    accept_payoff = max(edge_buy, edge_sell)
Counter with (ask, ask) only if force_payoff clears accept_payoff by a
1-tick margin — no ref bot does this at all. Combined with Change 1 and
tested together (isolating this specific increment): +0.09/deal and
+0.08/deal on top of Change 1 alone, same two seeds. Consistent direction,
correctly derived, mechanism independently verified against engine.py —
but a ~0.08/deal effect against ~10.5 std/deal needs far more than 300
deals to be a clean statistical proof. Shipping it because it's never
shown a negative signal and the derivation holds up, not because it's
proven at high confidence. Worth a bigger backtest run if you have spare
time before 11:59 PM.

NOT DONE — bidding "micro-jitters" (LOW priority, flagging why)
------------------------------------------------------------------------
Your original brief wanted bid randomization to resist reverse-engineering
across a multi-round match. That mattered before we knew the actual rule:
the engine re-executes your module from source and hands every deal a
FRESH Bot instance — confirmed in RULEBOOK.md SS8 and empirically via
--isolate — so no bot, including the opponent's, can adapt within a match
either. There's no live adaptive opponent to be reverse-engineered BY
during a match. It only matters if bot source is shared between rounds of
the tournament — unknown, wasn't in the rulebook. Cheap to add if you want
it anyway; skipped for now rather than spending TE-auction precision on a
threat that may not exist here.

WHAT'S UNCHANGED FROM my_bot_1 (see that file's docstring for the
original derivation): calibrated POWER_VALUES table, SHADE=0.60, TRANSFORM
flat-hand heuristic with DENIAL_WEIGHT=0.0 (still the one open number the
rulebook itself flags — still didn't guess a replacement), cross-round
opponent memory in _remember_opp() (+3.38/deal of my_bot_1's edge over
AdaptiveBidder traces back to this alone).
"""

import random

POWER_VALUES = {
    "FORESIGHT":    {1: 0.76, 2: 1.16, 3: 1.48, 4: 1.97, 5: 2.02},
    "TRICK_ROOM":   {1: 1.14, 2: 0.00, 3: 0.00, 4: 0.60, 5: 0.52},
    "SUBSTITUTE":   {1: 1.46, 2: 1.15, 3: 0.95, 4: 0.57, 5: 0.29},
    "STEALTH_ROCK": {1: 1.51, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.00},
    "TRANSFORM":    {1: 1.58, 2: 1.24, 3: 1.31, 4: 0.00, 5: 0.00},
}

SHADE = 0.60
FLAT_THRESHOLD = 1
OPP_FLAT_THRESHOLD = 2.0
DENIAL_WEIGHT = 0.0        # UNRESOLVED on this spec -- see docstring, re-measure if time allows
FORCE_MARGIN = 1.0         # required edge of forcing over best accept, in ticks -- not exhaustively tuned


class Bot:
    name = "my_bot_7"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)
        self._anchor = {}       # round -> our read of the opponent's revealed sum from their quote
        self._best_opp = None   # (round, quality, value) -- best opponent-sum read so far this deal

    # ---------- shared pricing ----------

    def _remember_opp(self, obs, quote=None):
        """Update the single best read of the opponent's revealed sum.

        A later round always supersedes an earlier one; at equal round, an
        exact FORESIGHT leak (quality 2) beats a round-5 sample (1) beats
        a quote-derived anchor (0). Nothing is blended by a fixed weight
        regardless of how stale or noisy it is.
        """
        candidates = []
        if obs.foresight:
            quality = 2 if obs.round <= 4 else 1   # rounds 1-4: complete leak, not a sample
            candidates.append((obs.round, quality, float(sum(obs.foresight))))
        if not obs.is_maker and quote is not None:
            r = obs.round
            if r not in self._anchor:
                self._anchor[r] = (quote[0] + quote[1]) / 2.0
            candidates.append((r, 0, self._anchor[r]))
        for cand in candidates:
            if self._best_opp is None or cand[:2] > self._best_opp[:2]:
                self._best_opp = cand

    def _value(self, obs, quote=None):
        """Point estimate of S given everything seen so far this deal."""
        self._remember_opp(obs, quote)
        opp = self._best_opp[2] if self._best_opp else 0.0
        return float(obs.k_mine + opp)

    def _power_value(self, obs, name):
        return POWER_VALUES.get(name, {}).get(obs.round, 0.5)

    def _transform_value(self, obs):
        swap = self._power_value(obs, "TRANSFORM")
        if abs(obs.k_mine) <= FLAT_THRESHOLD:
            return swap
        opp = self._best_opp[2] if self._best_opp else None
        if opp is not None and abs(opp) <= OPP_FLAT_THRESHOLD:
            return swap * DENIAL_WEIGHT
        return 0.0

    # ---------- required interface ----------

    def bid(self, obs, offered):
        if not offered or obs.te_mine <= 0:
            return {}
        out = {}
        for name in offered:
            v = self._transform_value(obs) if name == "TRANSFORM" else self._power_value(obs, name)
            if v <= 0:
                continue
            fair_te = v / self.config.TE_SALVAGE
            out[name] = max(0, min(int(fair_te * SHADE), obs.te_mine))
        return out

    def quote(self, obs):
        v = round(self._value(obs))
        cap = obs.final_cap
        lo = v - cap // 2
        return (lo, lo + cap)

    def respond(self, obs, quote, turn):
        """Force on the last turn if the math clears a real margin; otherwise accept on a real edge, else counter toward our value estimate."""
        bid, ask = quote
        v = self._value(obs, quote)
        edge_buy = v - ask
        edge_sell = bid - v
        thresh = -1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0

        if turn >= self.config.N_TURNS:
            shift_mine = sum(
                self.config.POWERS[n]["magnitude"]
                for n in ("TRICK_ROOM", "STEALTH_ROCK")
                if n in obs.powers_mine and n in self.config.POWERS
            )
            force_payoff = (ask - v) + shift_mine - self.config.FORCED_FILL_FEE
            accept_payoff = max(edge_buy, edge_sell)
            if force_payoff > accept_payoff + FORCE_MARGIN:
                return ("COUNTER", ask, ask)

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        w = max(obs.final_cap, (ask - bid) - self.config.MIN_REDUCTION)
        center = max(bid, min(round(v), ask - w))
        return ("COUNTER", center, center + w)

    def use_transform(self, obs):
        return abs(obs.k_mine) <= FLAT_THRESHOLD
