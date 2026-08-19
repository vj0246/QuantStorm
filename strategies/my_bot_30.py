# Name: Vivaan Jain
# College: Dwarkadas Jivanlal College of Engineering
# Roll Number: 60004240203

"""
starter_bot.py — Derived Ledger: QuantStorm 2026 (Maximal Exploitative Build)
=============================================================================
This build leverages a strict Game Theory Optimal (GTO) foundation and applies 
targeted Maximal Exploitative (ME) overlays to extract EV from flawed baselines.

EXPLOITS ENABLED:
1. Epsilon-Nudge Bidding: Breaks GTO ties by adding +1 TE, capped by opponent budget.
2. True Realized Midpoint: Evaluates Turn 6 traps using the engine's post-sanitization settlement price.
3. Edge Skimming: Shifts the counter-quote center by 1 tick to bleed impatient opponents.
"""

import math
import random

POWER_VALUES = {
    "FORESIGHT":    {1: 0.56, 2: 1.50, 3: 2.96, 4: 3.74, 5: 3.18},
    "TRICK_ROOM":   {1: 5.27, 2: 4.21, 3: 1.37, 4: 1.15, 5: 1.07},
    "SUBSTITUTE":   {1: 2.96, 2: 2.49, 3: 2.09, 4: 1.63, 5: 1.42},
    "STEALTH_ROCK": {1: 8.71, 2: 5.20, 3: 2.39, 4: 1.48, 5: 0.00},
    "TRANSFORM":    {1: 2.40, 2: 3.00, 3: 3.37, 4: 0.00, 5: 0.00},
}

FORCING_PRIOR_BY_ROUND = {1: 0.88, 2: 0.72, 3: 0.22, 4: 0.19, 5: 0.17}
PRIOR_WEIGHT = 3.0          

SHADE = 0.5                 
FORCE_SHIFT_POWERS = ("TRICK_ROOM", "STEALTH_ROCK")
TRANSFORM_SIGMA = 1.0       
FORCE_MARGIN = 1.0          

_EXPECTED_VALUE_BY_ROUND = {
    r: (sum(d.get(r, 0.0) for d in POWER_VALUES.values()) / len(POWER_VALUES))
    for r in range(1, 6)
}


class Bot:
    name = "my_bot_30"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)

    # ---------- opponent signal, recomputed fresh from obs every call ----------

    def _foresight_estimate(self, obs):
        if not obs.foresight:
            return None
        m = self.config.REVEAL_PER_ROUND * obs.round
        j = len(obs.foresight)
        if j <= 0:
            return None
        s = float(sum(obs.foresight))
        k_hat = s * m / j if j else 0.0
        var = m * (m - j) / j if j > 0 else float("inf")
        return (m, j, k_hat, var)

    def _quote_anchor(self, obs, quote=None):
        best = None 
        for c in obs.contracts:
            if c.maker_seat >= 0 and c.maker_seat != self.seat:
                coverage = self.config.REVEAL_PER_ROUND * c.round
                cand = (coverage, c.round, (c.open_bid + c.open_ask) / 2.0)
                if best is None or cand[:2] > best[:2]:
                    best = cand
        if not obs.is_maker and quote is not None:
            coverage = self.config.REVEAL_PER_ROUND * obs.round
            cand = (coverage, obs.round, (quote[0] + quote[1]) / 2.0)
            if best is None or cand[:2] > best[:2]:
                best = cand
        return best

    def _opp_value(self, obs, quote=None):
        fs = self._foresight_estimate(obs)
        anchor = self._quote_anchor(obs, quote)
        fs_coverage = fs[1] if fs else -1
        anchor_coverage = anchor[0] if anchor else -1
        if fs is not None and fs_coverage >= anchor_coverage:
            return fs[2]
        if anchor is not None:
            return anchor[2]
        return 0.0

    def _value(self, obs, quote=None):
        raw = obs.k_mine + self._opp_value(obs, quote)
        lo, hi = -float(self.config.N_COINS), float(self.config.N_COINS)
        return max(lo, min(raw, hi))

    # ---------- forcing-rate posterior ----------

    def _forcing_rate(self, obs):
        n = len(obs.contracts)
        forced = sum(1 for c in obs.contracts if c.forced)
        prior = FORCING_PRIOR_BY_ROUND.get(obs.round, 0.3)
        return (forced + PRIOR_WEIGHT * prior) / (n + PRIOR_WEIGHT), prior

    # ---------- power valuation ----------

    def _power_value(self, obs, name):
        base = POWER_VALUES.get(name, {}).get(obs.round, 0.5)
        if name in FORCE_SHIFT_POWERS:
            rate, prior = self._forcing_rate(obs)
            if prior > 0:
                base *= rate / prior
        return base

    def _flat_threshold_sd(self, obs, coins):
        return math.sqrt(max(coins, 1))

    def _transform_value(self, obs):
        swap = self._power_value(obs, "TRANSFORM")
        coins = self.config.REVEAL_PER_ROUND * obs.round
        if abs(obs.k_mine) <= TRANSFORM_SIGMA * self._flat_threshold_sd(obs, coins):
            return swap
        return 0.0

    # ---------- Maker width choice ----------

    def _best_width(self, obs, my_unseen):
        floor, cap = obs.final_cap, obs.spread_cap
        best_w, best_score = floor, None
        for w in range(floor, cap + 1):
            p_true = self.config.straddle_prob(obs.round, w, unseen=my_unseen)
            p_base = self.config.straddle_prob(obs.round, w)
            score = (self.config.MAKER_OBLIGATION * (p_true - p_base)
                     - self.config.WIDTH_PREMIUM * (w - floor))
            if best_score is None or score > best_score:
                best_score, best_w = score, w
        return best_w

    def _te_bid(self, obs, v):
        if v <= 0 or obs.te_mine <= 0:
            return 0
        ceiling_1 = (v / self.config.TE_SALVAGE) * SHADE

        future_reserve = sum(
            _EXPECTED_VALUE_BY_ROUND.get(r2, 0.0)
            for r2 in range(obs.round + 1, 6)
        )
        denom = v + future_reserve
        round_share = 1.0 if denom <= 0 else v / denom
        ceiling_2 = obs.te_mine * round_share

        bid_te = min(ceiling_1, ceiling_2)

        # EXPLOIT: The Epsilon Nudge. 
        # Add a flat +1 TE to the rounded GTO bid to deterministically break ties.
        aggressive_bid = round(bid_te) + 1
        
        # EXPLOIT: The Cap. Never bid more than mathematically necessary to bankrupt their remaining budget.
        max_required_to_win = obs.te_theirs + 1
        
        final_bid = min(aggressive_bid, max_required_to_win)
        return max(0, min(final_bid, obs.te_mine))

    # ---------- required interface ----------

    def bid(self, obs, offered):
        if not offered or obs.te_mine <= 0:
            return {}
        out = {}
        for name in offered:
            v = self._transform_value(obs) if name == "TRANSFORM" else self._power_value(obs, name)
            amt = self._te_bid(obs, v)
            if amt > 0:
                out[name] = amt
        return out

    def quote(self, obs):
        v = round(self._value(obs))

        fs = self._foresight_estimate(obs)
        j_seen = fs[1] if fs else 0
        base_unseen = self.config.N_COINS - self.config.REVEAL_PER_ROUND * obs.round
        my_unseen = max(0, base_unseen - j_seen)
        w = self._best_width(obs, my_unseen)

        lo = v - w // 2
        return (lo, lo + w)

    def respond(self, obs, quote, turn):
        bid, ask = quote

        # --- Hard rule: not the last turn, quote already at the round's floor width ---
        if turn < self.config.N_TURNS and (ask - bid) <= obs.final_cap:
            v = self._value(obs, quote)
            edge_buy = v - ask
            edge_sell = bid - v
            thresh = -1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0
            if edge_buy > thresh and edge_buy >= edge_sell:
                return "ACCEPT_BUY"
            if edge_sell > thresh:
                return "ACCEPT_SELL"
            return ("COUNTER", bid, ask)

        v = self._value(obs, quote)
        edge_buy = v - ask
        edge_sell = bid - v
        thresh = -1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0

        # --- Last turn: Countering triggers forced fill ---
        if turn >= self.config.N_TURNS:
            shift_mine = sum(
                self.config.POWERS[n]["magnitude"]
                for n in FORCE_SHIFT_POWERS
                if n in obs.powers_mine and n in self.config.POWERS
            )
            shift_theirs = sum(
                self.config.POWERS[n]["magnitude"]
                for n in FORCE_SHIFT_POWERS
                if n in obs.powers_theirs and n in self.config.POWERS
            )
            
            # EXPLOIT: True Realized Midpoint
            # When we return ("COUNTER", ask, ask), the engine sanitizes the bid to (ask - final_cap).
            realized_midpoint = ask - (obs.final_cap / 2.0)
            
            force_payoff = (realized_midpoint - v) + shift_mine - shift_theirs - self.config.FORCED_FILL_FEE
            accept_payoff = max(edge_buy, edge_sell)
            if force_payoff > accept_payoff + FORCE_MARGIN:
                return ("COUNTER", ask, ask)

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        w = max(obs.final_cap, (ask - bid) - self.config.MIN_REDUCTION)
        
        # EXPLOIT: Edge Skimming. Don't center exactly on v. 
        # Shift the center 1 tick in our favor to force the opponent to take a worse price.
        current_mid = (bid + ask) / 2.0
        skew = -1 if v > current_mid else 1 
        ideal_center = round(v) + skew
        
        center = max(bid, min(ideal_center, ask - w))
        return ("COUNTER", center, center + w)

    def use_transform(self, obs):
        coins = self.config.REVEAL_PER_ROUND * obs.round
        return abs(obs.k_mine) <= TRANSFORM_SIGMA * self._flat_threshold_sd(obs, coins)