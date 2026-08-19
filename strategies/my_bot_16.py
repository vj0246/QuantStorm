# Name: Vivaan Jain
# College: Dwarkadas Jivanlal Sanghvi College of Engineering
# Roll Number: 60004240203

"""
vj_apex_bot.py — Divided Oracle (QuantStorm 2026)
============================================================
The Apex Architecture: Fully generalized, strict Game Theory Optimal (GTO) 
bot with zero small-sample overfitting. Built entirely on derived relations, 
stateless cross-round memory, exact lattice parity quoting, and Beta-Binomial 
opponent profiling.
"""

import math
import random

POWER_VALUES = {
    "FORESIGHT":    {1: 0.76, 2: 1.16, 3: 1.48, 4: 1.97, 5: 2.02},
    "TRICK_ROOM":   {1: 1.14, 2: 0.00, 3: 0.00, 4: 0.60, 5: 0.52},
    "SUBSTITUTE":   {1: 1.46, 2: 1.15, 3: 0.95, 4: 0.57, 5: 0.29},
    "STEALTH_ROCK": {1: 1.51, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.00},
    "TRANSFORM":    {1: 1.58, 2: 1.24, 3: 1.31, 4: 0.00, 5: 0.00},
}                           

SHADE = 0.60                
FORCE_SHIFT_POWERS = ("TRICK_ROOM", "STEALTH_ROCK")
FORCING_PRIOR = 0.17        
PRIOR_WEIGHT = 3.0          
FLAT_FRACTION = 0.35        
DENIAL_WEIGHT = 0.0         
FORCE_MARGIN = 1.0          


class Bot:
    name = "my_bot_16"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)

    # ── Belief State ─────────────────────────────────────────────

    def _opp_best_signal(self, obs):
        """Stateless opponent memory: Derives the absolute highest-coverage 
        read available live from contracts and foresight. Never blends."""
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

    # ── Auction Layer ────────────────────────────────────────────

    def _forcing_rate(self, obs):
        """Beta-Binomial shrinkage toward a 17% forced-fill prior."""
        n = len(obs.contracts)
        forced = sum(1 for c in obs.contracts if c.forced)
        return (forced + PRIOR_WEIGHT * FORCING_PRIOR) / (n + PRIOR_WEIGHT)

    def _power_value(self, obs, name):
        base = POWER_VALUES.get(name, {}).get(obs.round, 0.5)
        if name in FORCE_SHIFT_POWERS:
            base *= self._forcing_rate(obs) / FORCING_PRIOR
        return base

    def _flat_threshold(self, obs, coins):
        """Scales transform threshold to the round's natural variance."""
        return FLAT_FRACTION * math.sqrt(max(coins, 1))

    def _transform_value(self, obs):
        swap = self._power_value(obs, "TRANSFORM")
        if abs(obs.k_mine) <= self._flat_threshold(obs, self.config.REVEAL_PER_ROUND * obs.round):
            return swap
        sig = self._opp_best_signal(obs)
        if sig is not None and abs(sig[2]) <= self._flat_threshold(obs, sig[1]):
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

    # ── Quote Layer ──────────────────────────────────────────────

    def _best_width(self, obs, my_unseen):
        """Exact lattice parity search for Maker Obligation maximum EV."""
        floor, cap = obs.final_cap, obs.spread_cap
        best_w, best_score = floor, float('-inf')
        for w in range(floor, cap + 1):
            p_true = self.config.straddle_prob(obs.round, w, unseen=my_unseen)
            p_base = self.config.straddle_prob(obs.round, w)
            score = self.config.MAKER_OBLIGATION * (p_true - p_base) - self.config.WIDTH_PREMIUM * (w - floor)
            if score > best_score:
                best_score, best_w = score, w
        return best_w

    def quote(self, obs):
        sig = self._opp_best_signal(obs)
        coins_known = sig[1] if sig else 0
        opp_val = sig[2] if sig else 0.0
        v = round(obs.k_mine + opp_val)

        base_unseen = self.config.N_COINS - self.config.REVEAL_PER_ROUND * obs.round
        my_unseen = max(0, base_unseen - coins_known)

        if "FORESIGHT" in obs.powers_theirs:
            # Adverse Selection Defense
            w = obs.spread_cap
        else:
            w = self._best_width(obs, my_unseen)

        lo = v - w // 2
        return (lo, lo + w)

    # ── Negotiation Layer ────────────────────────────────────────

    def respond(self, obs, quote, turn):
        bid, ask = quote
        v = self._value(obs, quote)
        edge_buy = v - ask
        edge_sell = bid - v
        thresh = -1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0

        if turn >= self.config.N_TURNS:
            # Turn 6 Forcing Trap: Calculates absolute price shift from both players
            shift = sum(self.config.POWERS.get(n, {}).get("magnitude", 0) for n in FORCE_SHIFT_POWERS if n in obs.powers_mine)
            shift -= sum(self.config.POWERS.get(n, {}).get("magnitude", 0) for n in FORCE_SHIFT_POWERS if n in obs.powers_theirs)
            
            force_payoff = (ask - v) + shift - self.config.FORCED_FILL_FEE
            accept_payoff = max(edge_buy, edge_sell)
            
            if force_payoff > accept_payoff + FORCE_MARGIN:
                return ("COUNTER", ask, ask)

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        # General Counter: Shrink safely bounded by engine floor
        w = max(obs.final_cap, (ask - bid) - self.config.MIN_REDUCTION)
        center = max(bid, min(round(v), ask - w))
        return ("COUNTER", center, center + w)

    def use_transform(self, obs):
        return abs(obs.k_mine) <= self._flat_threshold(obs, self.config.REVEAL_PER_ROUND * obs.round)