# Name: Vivaan Jain
# College: Dwarkadas Jivanlal Sanghvi College of Engineering
# Roll Number: 60004240203

"""
vj_bot_v7.py -- The Harvester
==============================================================
Incorporates flawless Bayesian math while actively hunting and exploiting 
the flaws of weaker leaderboard bots (Passive Sniping, Coward Squeezing, 
and TRANSFORM defense).
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
FORCING_PRIOR = 0.17
PRIOR_WEIGHT = 3.0


class Bot:
    name = "my_bot_8"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)
        self._anchor = {}
        self._best_opp = None   

    # ── Belief State ──────────────────────────────────────────────

    def _remember_opp(self, obs, quote=None):
        candidates = []
        if obs.foresight:
            quality = 2 if obs.round <= 4 else 1   
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
        self._remember_opp(obs, quote)
        opp = self._best_opp[2] if self._best_opp else 0.0
        return float(obs.k_mine + opp)

    # ── Opponent Profiling (The Exploits) ─────────────────────────

    def _prob_early_accept(self, obs):
        """Detects 'Coward' bots that immediately accept opening quotes."""
        if not obs.contracts:
            return 0.0
        early_accepts = sum(1 for c in obs.contracts if c.maker_seat == self.seat and not c.forced and (c.price == c.open_ask or c.price == c.open_bid))
        maker_rounds = sum(1 for c in obs.contracts if c.maker_seat == self.seat)
        return early_accepts / maker_rounds if maker_rounds > 0 else 0.0

    def _estimate_opp_max_bid(self, obs):
        """Detects 'Passive' bots to avoid wasting TE."""
        spent = self.config.TE_BUDGET - obs.te_theirs
        if obs.round == 1:
            return self.config.TE_BUDGET
        if spent == 0:
            return 1  # Snipe for 1 TE if they never bid
        wins = len(obs.powers_theirs)
        if wins == 0:
            return self.config.TE_BUDGET
        return int((spent / wins) * 1.5)

    def _opponent_forcing_rate(self, obs):
        n = len(obs.contracts)
        forced = sum(1 for c in obs.contracts if c.forced)
        return (forced + PRIOR_WEIGHT * FORCING_PRIOR) / (n + PRIOR_WEIGHT)

    # ── Auction Layer ────────────────────────────────────────────

    def _transform_value(self, obs):
        """Dynamic TRANSFORM valuation: Swap if flat, Defend if decisive."""
        my_decisiveness = abs(obs.k_mine)
        # Expected absolute sum of their residual hand
        n_unseen_theirs = 20 - 4 * obs.round
        expected_abs_theirs = (2 * n_unseen_theirs / 3.14159)**0.5 if n_unseen_theirs > 0 else 0
        opp_read = abs(self._best_opp[2]) if self._best_opp else expected_abs_theirs

        if my_decisiveness <= FLAT_THRESHOLD:
            # Value of swapping our flat hand for their potentially better one
            return max(0.0, opp_read - my_decisiveness)
        else:
            # Value of defending our decisive hand from being stolen
            return max(0.0, my_decisiveness - opp_read)

    def _power_value(self, obs, name):
        if name == "TRANSFORM":
            return self._transform_value(obs)
        base = POWER_VALUES.get(name, {}).get(obs.round, 0.5)
        if name in ("TRICK_ROOM", "STEALTH_ROCK") and base > 0:
            base *= self._opponent_forcing_rate(obs) / FORCING_PRIOR
        return base

    def bid(self, obs, offered):
        if not offered or obs.te_mine <= 0:
            return {}
        out = {}
        for name in offered:
            v = self._power_value(obs, name)
            if v <= 0:
                continue
            
            fair_te = v / self.config.TE_SALVAGE
            
            # Capping bids against passive opponents to hoard TE
            opp_max = self._estimate_opp_max_bid(obs)
            cap_bid = min(obs.te_mine, opp_max + 1)
            
            jitter = self.rng.uniform(-0.02, 0.02)
            amt = max(0, min(int(fair_te * (SHADE + jitter)), cap_bid))
            if amt > 0:
                out[name] = amt
        return out

    # ── Quote Layer ──────────────────────────────────────────────

    def _best_width(self, obs, center, unseen_true):
        cfg = self.config
        floor, cap = cfg.final_cap(obs.round), cfg.spread_cap(obs.round)
        lam, prem = cfg.MAKER_OBLIGATION, cfg.WIDTH_PREMIUM
        p_accept = self._prob_early_accept(obs)
        
        best_w, best_ev = floor, float("-inf")
        for w in range(floor, cap + 1):
            p_base = cfg.straddle_prob(obs.round, w)
            p_true = p_base if unseen_true is None else cfg.straddle_prob(obs.round, w, unseen=unseen_true)
            
            # EV = Obligation EV + Expected Execution Edge (if they accept early)
            ev_obl = lam * (p_true - p_base) - prem * (w - floor)
            ev_exec = p_accept * (w / 2.0) 
            
            ev = ev_obl + ev_exec
            if ev > best_ev + 1e-9:
                best_ev, best_w = ev, w
        return best_w

    def quote(self, obs):
        s_hat = self._value(obs)
        r = obs.round

        if "FORESIGHT" in obs.powers_theirs:
            width = self.config.spread_cap(r)
        else:
            unseen_true = None
            if obs.foresight:
                base_unseen = self.config.N_COINS - self.config.REVEAL_PER_ROUND * r
                unseen_true = max(0, base_unseen - len(obs.foresight))
            width = self._best_width(obs, s_hat, unseen_true)

        lo = round(s_hat) - width // 2
        return (lo, lo + width)

    # ── Negotiation Layer ────────────────────────────────────────

    def respond(self, obs, quote, turn):
        bid, ask = quote
        v = self._value(obs, quote)

        edge_buy = v - ask
        edge_sell = bid - v
        thresh = (-1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0) + self.rng.gauss(0.0, 0.25)

        if turn == self.config.N_TURNS:
            fee = self.config.FORCED_FILL_FEE
            w = max(0, (ask - bid) - self.config.MIN_REDUCTION)
            w = max(self.config.final_cap(obs.round), w) 
            forced_bid, forced_ask = ask - w, ask   
            
            shift = sum(self.config.POWERS.get(p, {}).get("magnitude", 0) for p in obs.powers_mine if p in ("TRICK_ROOM", "STEALTH_ROCK"))
            shift -= sum(self.config.POWERS.get(p, {}).get("magnitude", 0) for p in obs.powers_theirs if p in ("TRICK_ROOM", "STEALTH_ROCK"))
            
            forced_price = (forced_bid + forced_ask) // 2 + shift
            counter_pnl = forced_price - v - fee

            best = max(edge_buy, edge_sell, counter_pnl)
            if best == counter_pnl:
                return ("COUNTER", forced_bid, forced_ask)
            return "ACCEPT_BUY" if edge_buy >= edge_sell else "ACCEPT_SELL"

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        w = max(0, (ask - bid) - self.config.MIN_REDUCTION)
        center = max(bid, min(round(v), ask - w))
        return ("COUNTER", center, center + w)

    def use_transform(self, obs):
        return abs(obs.k_mine) <= FLAT_THRESHOLD