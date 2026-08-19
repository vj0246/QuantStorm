# Name: Vivaan Jain
# College: Dwarkadas Jivanlal College of Engineering
# Roll Number: 60004240203

"""
my_bot.py -- Divided Oracle strategy (QuantStorm 2026, Round 1)

quote()   Center on k_mine + foresight (exact posterior mean/median for a
          symmetric +/-1 residual). Width: argmax over the legal integer
          widths [final_cap, spread_cap] of

              3 * (straddle_prob(unseen=my_true_unseen)
                   - straddle_prob(unseen=baseline_unseen))
              - WIDTH_PREMIUM * (width - final_cap)

          Both probabilities come from config.straddle_prob(), exactly.
          With no FORESIGHT held, my_true_unseen == baseline_unseen, the
          bracket is 0 for every width, and the argmax collapses to the
          floor (final_cap) -- WIDTH_PREMIUM is a pure cost with nothing
          to offset it once the two straddle rates match, which is what
          the Maker-obligation math in RULEBOOK.md #7.2 actually says once
          its two components are separated. With FORESIGHT held, my true
          straddle rate is provably higher than the rate the obligation is
          scored against, and the gap is real, computable edge that the
          loop below claims. Deliberately not hand-rolling a Gaussian
          approximation: the residual is an exact lattice (straddle_prob
          is exact, not erf), and RULEBOOK.md's own note on lattice parity
          says using the exact function instead of a smooth approximation
          is worth real ticks on its own.

respond() Accept if the live range no longer contains my value estimate;
          otherwise counter toward it, shrinking by MIN_REDUCTION. On the
          final turn, countering is not "keep negotiating" -- it is a
          fully determined outcome (forced midpoint + shift, minus the
          forcing fee) -- so it is priced as a third concrete option and
          the best of the three is taken unconditionally, rather than
          countering by default whenever neither accept clears a bar.

bid()     Round-conditional power values, carried over from
          strategies/adaptive_bidder.py (RULEBOOK.md #12 explicitly
          permits reusing the provided reference bots' constants).
          TRANSFORM is priced asymmetrically (fire from a flat hand, pay
          to deny from a decisive one only when the opponent also reads
          flat) -- see DENIAL_WEIGHT below for what changed from the
          reference value and why.

use_transform()  Fire below FLAT_THRESHOLD, decline otherwise. Both
          reference bots and RULEBOOK.md's own worked example agree this
          dominates.

No cross-deal state anywhere: reset() is the only place per-deal state is
created, matching RULEBOOK.md #8's enforced statelessness. Bidding is
deterministic rather than mixed on purpose: bids are blind and
simultaneous within a round, and no bot -- mine or the opponent's -- can
carry a read on my pattern across deals, so there is no repeated-game
exploitation channel here for a fixed policy to defend against the way
there would be in a sequential, memoryful game.
"""

from __future__ import annotations
import random

# Carried over from strategies/adaptive_bidder.py -- calibrated tick value
# per power, per round. RULEBOOK.md #12 permits reusing shipped constants.
POWER_VALUES = {
    "FORESIGHT":    {1: 0.76, 2: 1.16, 3: 1.48, 4: 1.97, 5: 2.02},
    "TRICK_ROOM":   {1: 1.14, 2: 0.00, 3: 0.00, 4: 0.60, 5: 0.52},
    "SUBSTITUTE":   {1: 1.46, 2: 1.15, 3: 0.95, 4: 0.57, 5: 0.29},
    "STEALTH_ROCK": {1: 1.51, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.00},
    "TRANSFORM":    {1: 1.58, 2: 1.24, 3: 1.31, 4: 0.00, 5: 0.00},
}
SHADE = 0.60              # first-price shade; adaptive_bidder's swept value
FLAT_THRESHOLD = 1
OPP_FLAT_THRESHOLD = 2.0
# adaptive_bidder ships this at 0.0, measured on a since-changed spec
# (TE_BUDGET=40, where the swap was worth ~22% of the whole budget vs.
# 65-85% now at TE_BUDGET=24) -- that compression looked like a reason
# to re-test rather than trust the stale number. Swept 0.0/0.3/0.6/1.0/1.5
# against adaptive_bidder (n=800, seed 7): +0.69/+0.64/+0.64/+0.55/+0.55
# ticks/deal -- monotonically *worse* as denial spend increases. The
# theory said the ratio shift made denial worth another look; the data
# said look and confirm it still isn't worth it. Going with the data.
DENIAL_WEIGHT = 0.0

SHIFT_MAGNITUDE = {"TRICK_ROOM": 3, "STEALTH_ROCK": 2}


class Bot:
    name = "my_bot_5"

    def reset(self, seat: int, config, seed: int) -> None:
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)
        self._anchor: dict[int, float] = {}

    # ---------------------------------------------------------- pricing --

    def _value(self, obs) -> float:
        """Point estimate of S from everything visible so far this deal."""
        v = obs.k_mine + sum(obs.foresight)
        a = self._anchor.get(obs.round)
        if a is not None:
            v += a
        return float(v)

    def _opp_open(self, obs):
        """Most recent settled round in which the opponent was Maker."""
        best = None
        for c in obs.contracts:
            if c.maker_seat != self.seat and (best is None or c.round > best.round):
                best = c
        return best

    # -------------------------------------------------------- quote (T1) --

    def quote(self, obs):
        center = round(obs.k_mine + sum(obs.foresight))
        if "FORESIGHT" in obs.powers_theirs:
            # They can see into my revealed coins; my honest quote is now
            # gameable by someone with a better read on S than the
            # baseline I priced the argmax against. Blunt but measured
            # positive vs adaptive_bidder (+0.53 -> +0.68 ticks/deal avg,
            # n=2000, 2 seeds) -- a graded response (interpolate rather
            # than jump straight to the cap) is the natural next refinement,
            # untested here.
            width = obs.spread_cap
            lo = center - width // 2
            return (lo, lo + width)
        baseline_unseen = self.config.N_COINS - self.config.REVEAL_PER_ROUND * obs.round
        my_unseen = baseline_unseen - len(obs.foresight)
        floor = obs.final_cap

        best_w, best_ev = floor, float("-inf")
        for w in range(floor, obs.spread_cap + 1):
            p_true = self.config.straddle_prob(obs.round, width=w, unseen=my_unseen)
            p_base = self.config.straddle_prob(obs.round, width=w)
            ev = 3.0 * (p_true - p_base) - self.config.WIDTH_PREMIUM * (w - floor)
            if ev > best_ev:
                best_ev, best_w = ev, w

        lo = center - best_w // 2
        return (lo, lo + best_w)

    # ---------------------------------------------------- respond (T2-6) --

    def respond(self, obs, quote, turn):
        bid, ask = quote
        if turn == 2:
            self._anchor[obs.round] = (bid + ask) / 2.0

        v = self._value(obs)
        edge_buy = v - ask
        edge_sell = bid - v
        w = max(0, (ask - bid) - self.config.MIN_REDUCTION)
        center = max(bid, min(round(v), ask - w))

        if turn == self.config.N_TURNS:
            # Forcing here settles at the midpoint of *my* proposed range,
            # and being the forcer makes me the seller -- so once forcing
            # is on the table, only the top of the legal range matters,
            # not honest centering (that was already priced into edge_buy/
            # edge_sell above). Measured: +0.66 ticks/deal vs rational
            # (n=2000, 2 seeds), exactly neutral vs adaptive_bidder (that
            # matchup essentially never reaches a forced turn-6 in the
            # first place).
            forced_bid, forced_ask = ask - w, ask
            shift = sum(SHIFT_MAGNITUDE.get(p, 0) for p in obs.powers_mine)
            shift -= sum(SHIFT_MAGNITUDE.get(p, 0) for p in obs.powers_theirs)
            forced_price = (forced_bid + forced_ask) // 2 + shift
            counter_pnl = forced_price - v - self.config.FORCED_FILL_FEE
            best = max(edge_buy, edge_sell, counter_pnl)
            if best == counter_pnl:
                return ("COUNTER", forced_bid, forced_ask)
            return "ACCEPT_BUY" if edge_buy >= edge_sell else "ACCEPT_SELL"

        thresh = -1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0
        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"
        return ("COUNTER", center, center + w)

    # ---------------------------------------------------------------- bid --

    def _power_value(self, obs, name):
        return POWER_VALUES.get(name, {}).get(obs.round, 0.5)

    def _transform_value(self, obs):
        swap = self._power_value(obs, "TRANSFORM")
        if abs(obs.k_mine) <= FLAT_THRESHOLD:
            return swap
        c = self._opp_open(obs)
        if c is not None and abs((c.open_bid + c.open_ask) / 2.0) <= OPP_FLAT_THRESHOLD:
            return swap * DENIAL_WEIGHT
        return 0.0

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

    # -------------------------------------------------------- transform --

    def use_transform(self, obs) -> bool:
        return abs(obs.k_mine) <= FLAT_THRESHOLD
