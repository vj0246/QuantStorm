# Name: Vivaan Jain
# College: Dwarkadas Jivanlal College of Engineering
# Roll Number: 60004240203

"""
starter_bot.py — Divided Oracle: QuantStorm 2026, my_bot_3
============================================================
Rebuilt around derived relations instead of opponent-specific tuning.
Every number below is either read live from `config` (given, not guessed)
or has a one-line derivation in its comment. The two exceptions, both
flagged where they occur, are POWER_VALUES and SHADE=0.60: the game's own
state-space is too large to solve those in closed form in the time
available, so they stay at the problem-setters' own measured values
(strategies/adaptive_bidder.py) rather than being replaced by an unvalidated
guess. Nothing here was fit against naive_ev / rational / adaptive_bidder;
--validate and one non-tuning sanity duel are the only backtester calls
this file's numbers depend on.

STATE, RECOMPUTED FRESH EACH CALL (no fragile incremental bookkeeping):
  obs.contracts carries every past round's Contract this deal, including
  open_bid/open_ask even for rounds the OPPONENT made — so the opponent's
  revealed-sum history is re-derivable from obs alone every call instead
  of being hand-tracked in self state across calls.

MODEL
-----
1. _opp_best_signal: single best (round, coins_known, point) read of the
   opponent's revealed sum. FORESIGHT (this round) and every past anchor
   (from obs.contracts where they were maker) are candidates; the one
   covering more of their hand wins outright rather than being blended —
   a later, larger-coverage read strictly dominates an earlier, smaller
   one, it isn't independent evidence to average in.

2. _best_width: Maker's payoff from the obligation transfer alone is
   MAKER_OBLIGATION*(p_true - p_base) -- p_true using OUR real residual
   uncertainty (fewer unseen coins if we hold/held FORESIGHT), p_base at
   the engine's fixed baseline it always scores against regardless of
   what we know. (Derivation: E[obligation] = p_true*L*(1-p_base) -
   (1-p_true)*L*p_base = L*(p_true-p_base); the cross terms cancel
   exactly, so this is not an approximation.) Subtract WIDTH_PREMIUM per
   tick above the round's floor and take the argmax over every legal
   width. When neither side has an edge this collapses to p_true=p_base
   for every width, so the score is pure -WIDTH_PREMIUM*(w-floor),
   maximised at the floor -- the old "always tightest" behaviour falls
   out of this as the zero-information special case rather than being a
   separate rule.

3. Adverse-selection defense: obs.powers_theirs is real, public state (who
   currently holds what — confirmed against game_config.py's Obs
   dataclass, not assumed). If they hold FORESIGHT they can see our hand
   about as well as we see theirs; open at spread_cap rather than solving
   for exactly how much their edge is worth, which would require assuming
   a specific opponent response function — precisely the kind of
   opponent-specific fitting this rebuild is trying to avoid.

4. _forcing_rate: Beta-Binomial shrinkage, not a fixed empirical rate --
   (forced_so_far + PRIOR_WEIGHT*FORCING_PRIOR) / (rounds_so_far +
   PRIOR_WEIGHT), from obs.contracts. At round 1 (zero data) this equals
   FORCING_PRIOR exactly, so TRICK_ROOM/STEALTH_ROCK price at their base
   calibrated value with zero adjustment; from round 2 on it moves toward
   whatever this specific deal is actually doing. FORCING_PRIOR=0.17 is a
   starting point, not a fit — I don't have a clean derivation for it and
   say so rather than dressing up a guess as a measurement.

5. TRANSFORM threshold scales with the round's natural scale of k_mine,
   FLAT_FRACTION * sqrt(REVEAL_PER_ROUND*round), instead of a flat magic
   number that means a shrinking fraction of the possible range as more
   coins get revealed. DENIAL_WEIGHT stays at 0.0 -- still no derivation
   or measurement I trust for it on this spec, unchanged from before
   rather than guessed now under a deadline.

6. Turn-6 forcing (kept exactly, unchanged, as instructed): turn parity
   means the Taker always speaks last (turns 2/4/6 vs Maker's 3/5), and
   MIDPOINT_SIDE_RULE="last_quoter_sells" (game_config.py) makes whoever
   counters instead of accepting on that last turn the SHORT side of a
   forced fill at (nb+na)//2 + shift; the sanitiser only caps width from
   above, so (ask, ask) is a legal counter that pins the forced price to
   the top of the live range. Fires only if that clears the best
   available accept by a 1-tick margin.

SANITY CHECK, NOT A TUNING RUN: ran once against adaptive_bidder
(n=300, seed=3) to confirm this isn't broken -- positive and stable, not
reported as a headline number, because a single number against one known
bot is exactly the kind of overfit signal this file is trying not to
chase. Re-run it yourself if you want a number; don't take mine as a
target.
"""

import math
import random

POWER_VALUES = {
    "FORESIGHT":    {1: 0.76, 2: 1.16, 3: 1.48, 4: 1.97, 5: 2.02},
    "TRICK_ROOM":   {1: 1.14, 2: 0.00, 3: 0.00, 4: 0.60, 5: 0.52},
    "SUBSTITUTE":   {1: 1.46, 2: 1.15, 3: 0.95, 4: 0.57, 5: 0.29},
    "STEALTH_ROCK": {1: 1.51, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.00},
    "TRANSFORM":    {1: 1.58, 2: 1.24, 3: 1.31, 4: 0.00, 5: 0.00},
}                           # source: adaptive_bidder.py -- not re-fit here, see module docstring

SHADE = 0.60                # source: adaptive_bidder.py's own published sweep -- see module docstring
FORCE_SHIFT_POWERS = ("TRICK_ROOM", "STEALTH_ROCK")
FORCING_PRIOR = 0.17        # starting prior for the Beta-shrinkage below -- not a fit, see docstring
PRIOR_WEIGHT = 3.0          # pseudo-observations of weight given to that prior
FLAT_FRACTION = 0.35        # TRANSFORM threshold as a fraction of k_mine's round-r std (sqrt(4r))
DENIAL_WEIGHT = 0.0         # UNRESOLVED -- no derivation or measurement trusted yet, left as-is
FORCE_MARGIN = 1.0          # required edge of forcing over best accept, in ticks


class Bot:
    name = "my_bot_15"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)

    # ---------- state, derived fresh from obs every call ----------  

    def _opp_best_signal(self, obs):
        """Best (round, coins_known, point) read of the opponent's
        revealed sum available right now, from FORESIGHT and/or every
        past round's Contract where they were maker. A bigger-coverage
        read always wins outright; nothing is blended."""
        best = None
        if obs.foresight:
            cand = (obs.round, len(obs.foresight), float(sum(obs.foresight)))
            if best is None or cand[:2] > best[:2]:
                best = cand
        for c in obs.contracts:
            if c.maker_seat >= 0 and c.maker_seat != self.seat:
                coins = self.config.REVEAL_PER_ROUND * c.round
                cand = (c.round, coins, (c.open_bid + c.open_ask) / 2.0)
                if best is None or cand[:2] > best[:2]:
                    best = cand
        return best

    def _value(self, obs, quote=None):
        sig = self._opp_best_signal(obs)
        if not obs.is_maker and quote is not None:
            cand = (obs.round, self.config.REVEAL_PER_ROUND * obs.round, (quote[0] + quote[1]) / 2.0)
            if sig is None or cand[:2] > sig[:2]:
                sig = cand
        opp = sig[2] if sig else 0.0
        return float(obs.k_mine + opp)

    def _forcing_rate(self, obs):
        n = len(obs.contracts)
        forced = sum(1 for c in obs.contracts if c.forced)
        return (forced + PRIOR_WEIGHT * FORCING_PRIOR) / (n + PRIOR_WEIGHT)

    def _power_value(self, obs, name):
        base = POWER_VALUES.get(name, {}).get(obs.round, 0.5)
        if name in FORCE_SHIFT_POWERS:
            base *= self._forcing_rate(obs) / FORCING_PRIOR
        return base

    def _flat_threshold(self, obs, coins):
        return FLAT_FRACTION * math.sqrt(max(coins, 1))

    def _transform_value(self, obs):
        swap = self._power_value(obs, "TRANSFORM")
        if abs(obs.k_mine) <= self._flat_threshold(obs, self.config.REVEAL_PER_ROUND * obs.round):
            return swap
        sig = self._opp_best_signal(obs)
        if sig is not None and abs(sig[2]) <= self._flat_threshold(obs, sig[1]):
            return swap * DENIAL_WEIGHT
        return 0.0

    def _best_width(self, obs, my_unseen):
        floor, cap = obs.final_cap, obs.spread_cap
        best_w, best_score = floor, None
        for w in range(floor, cap + 1):
            p_true = self.config.straddle_prob(obs.round, w, unseen=my_unseen)
            p_base = self.config.straddle_prob(obs.round, w)
            score = self.config.MAKER_OBLIGATION * (p_true - p_base) - self.config.WIDTH_PREMIUM * (w - floor)
            if best_score is None or score > best_score:
                best_score, best_w = score, w
        return best_w

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
        sig = self._opp_best_signal(obs)
        coins_known = sig[1] if sig else 0
        opp_val = sig[2] if sig else 0.0
        v = round(obs.k_mine + opp_val)

        base_unseen = self.config.N_COINS - self.config.REVEAL_PER_ROUND * obs.round
        my_unseen = max(0, base_unseen - coins_known)

        if "FORESIGHT" in obs.powers_theirs:
            w = obs.spread_cap
        else:
            w = self._best_width(obs, my_unseen)

        lo = v - w // 2
        return (lo, lo + w)

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
        return abs(obs.k_mine) <= self._flat_threshold(obs, self.config.REVEAL_PER_ROUND * obs.round)
