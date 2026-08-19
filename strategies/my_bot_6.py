# Name: Vivaan Jain
# College: Dwarkadas Jivanlal College of Engineering
# Roll Number: 60004240203 

"""
vj_bot.py -- Divided Oracle entry (QuantStorm 2026, Round 1)
==============================================================

Design summary:

  * S-estimate: k_mine (exact) + the single best opponent read available.
    FORESIGHT, when held, is a COMPLETE reveal of the opponent's
    currently-revealed coins in rounds 1-4 -- its magnitude (16) only
    starts to bind as a genuine subsample once 4*round > 16, i.e. round
    5 -- so it overwrites any older read rather than blending with it.
    Absent that, an honest Maker's opening-quote midpoint (latched once,
    on first sight, per round) is the fallback read. Never accumulated
    across rounds: obs.foresight is the SAME object on every call within
    one round (sampled once, before the negotiation starts), so summing
    it in more than once would double- and triple-count the same coins.
    One value per round key, always overwritten, never added to.

  * Quote width: the maker obligation pays
        lam*(p_true - p_baseline) - premium*(width - floor).
    At baseline information p_true == p_baseline for every width, so the
    premium term alone decides it and the tightest legal width always
    wins. A same-round FORESIGHT reveal makes p_true genuinely
    width-dependent, and because straddle_prob is an exact lattice sum
    rather than a smooth curve, the optimum sometimes lands on an odd
    width (RULEBOOK.md flags this as a parity effect on purpose) --
    solved by search over every admissible width with the engine's own
    exact straddle_prob, not assumed.
    Exception: if the OPPONENT holds FORESIGHT this round, they may know
    more about the residual than we do (they can see up to 16 of OUR
    revealed coins) -- a contract-level adverse-selection risk the
    obligation formula does not price at all -- so we widen to the cap
    instead of optimising.

  * Auction: POWER_VALUES is strategies/adaptive_bidder.py's measured
    per-round tick table, reused as shipped (RULEBOOK.md section 12:
    the code in this repo, constants included, is free to copy). SHADE
    follows the same reference. DENIAL_WEIGHT -- the one number that
    module explicitly calls unmeasured on the current 24-TE spec -- is
    picked from a direct sweep against the two strongest baselines in
    this repo (see accompanying chat message for the numbers), not a
    guess.

  * Final negotiation turn: whoever responds when turn == n_turns, if
    they counter, becomes the short side AND the forcer (last_quoter_
    sells) regardless of Maker/Taker role, and pays FORCED_FILL_FEE. So
    that turn explicitly prices three real outcomes -- accept-buy,
    accept-sell, force -- instead of the generic accept-if-edge
    heuristic used on every other turn, and if forcing wins, pins the
    shrunk counter to the ASK edge: legal under the shrink rule, and
    strictly the best midpoint obtainable, since forcing always puts us
    short.

Not implemented, on purpose, given the 24-hour / stateless-per-deal
format: no opponent model that persists across deals (the harness wipes
all bot state before every deal -- RULEBOOK.md section 8 -- so there is
nothing to persist one INTO), no CFR/RNR exploitation layer, no
deception layer. Within a single 5-round deal there are at most 2-3 real
reads on the opponent -- not enough signal for either, and honest,
accurate play already IS the equilibrium line once the maker obligation
is priced correctly (RULEBOOK.md section 4 withdraws the claim that
distorting a quote beats honest quoting).
"""

import random


# Measured per-round tick value of each power (source: adaptive_bidder.py).
POWER_VALUES = {
    "FORESIGHT":    {1: 0.76, 2: 1.16, 3: 1.48, 4: 1.97, 5: 2.02},
    "TRICK_ROOM":   {1: 1.14, 2: 0.00, 3: 0.00, 4: 0.60, 5: 0.52},
    "SUBSTITUTE":   {1: 1.46, 2: 1.15, 3: 0.95, 4: 0.57, 5: 0.29},
    "STEALTH_ROCK": {1: 1.51, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.00},
    "TRANSFORM":    {1: 1.58, 2: 1.24, 3: 1.31, 4: 0.00, 5: 0.00},
}
SHADE = 0.62               # fraction of fair value bid; reference basin 0.55-0.65
FLAT_THRESHOLD = 1         # |k_mine| at or below this: hand is "flat"
OPP_FLAT_THRESHOLD = 2.0   # |opponent read| at or below this: they look flat
DENIAL_WEIGHT = 1.0        # swap-value multiplier when denying a flat-looking opponent.
                            # Swept 0.0-1.5 against rational.py and adaptive_bidder.py
                            # (5 seeds x 30 deals each): the whole range moves the result
                            # by under 0.1 ticks/deal, inside the seed-to-seed noise -- the
                            # scenario (TRANSFORM drawn AND our hand decisive AND we have a
                            # read AND that read says they're flat) is just rare. 1.0 is the
                            # mild empirical peak, and it's the theoretically clean point:
                            # denying a flat opponent the swap protects our own decisive hand
                            # from being taken, which is worth about what the swap itself is
                            # worth. Not the 0.0 the old 40-TE-spec measurement in
                            # adaptive_bidder.py found -- that ratio (budget=3.2 ticks vs a
                            # ~0.85-tick swap) doesn't hold at 24 TE (budget=1.92 vs a
                            # 1.2-1.6-tick swap).


class Bot:
    name = "my_bot_6"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)
        self._opp_anchor = {}   # round -> best current read of opponent's k_theirs(round)

    # ---------------- shared belief ----------------

    def _update_opponent_read(self, obs):
        """Overwrite (never accumulate) this round's opponent read from FORESIGHT."""
        if obs.foresight:
            self._opp_anchor[obs.round] = float(sum(obs.foresight))

    def _record_maker_quote(self, obs, quote):
        """Latch the opponent's opening quote midpoint, once, the first time we see it."""
        r = obs.round
        if not obs.is_maker and r not in self._opp_anchor and quote is not None:
            self._opp_anchor[r] = (quote[0] + quote[1]) / 2.0

    def _opponent_component(self):
        if not self._opp_anchor:
            return 0.0
        return self._opp_anchor[max(self._opp_anchor)]

    def _estimate_S(self, obs):
        self._update_opponent_read(obs)
        return obs.k_mine + self._opponent_component()

    # ---------------- bid() ----------------

    def _transform_value(self, obs):
        swap = POWER_VALUES["TRANSFORM"].get(obs.round, 0.0)
        if abs(obs.k_mine) <= FLAT_THRESHOLD:
            return swap
        if self._opp_anchor:
            opp = self._opponent_component()
            if abs(opp) <= OPP_FLAT_THRESHOLD:
                return swap * DENIAL_WEIGHT
        return 0.0

    def _power_value(self, obs, name):
        if name == "TRANSFORM":
            return self._transform_value(obs)
        return POWER_VALUES.get(name, {}).get(obs.round, 0.5)

    def bid(self, obs, offered):
        if not offered or obs.te_mine <= 0:
            return {}
        out = {}
        for name in offered:
            v = self._power_value(obs, name)
            if v <= 0:
                continue
            fair_te = v / self.config.TE_SALVAGE
            amt = max(0, min(int(fair_te * SHADE), obs.te_mine))
            if amt > 0:
                out[name] = amt
        return out

    # ---------------- quote() ----------------

    def _best_width(self, r, unseen_true):
        """Width in [final_cap, spread_cap] maximising expected maker obligation."""
        cfg = self.config
        floor, cap = cfg.final_cap(r), cfg.spread_cap(r)
        lam, prem = cfg.MAKER_OBLIGATION, cfg.WIDTH_PREMIUM
        best_w, best_ev = floor, float("-inf")
        for w in range(floor, cap + 1):
            p_base = cfg.straddle_prob(r, w)
            p_true = p_base if unseen_true is None else cfg.straddle_prob(r, w, unseen=unseen_true)
            ev = lam * (p_true - p_base) - prem * (w - floor)
            if ev > best_ev + 1e-9:
                best_ev, best_w = ev, w
        return best_w

    def quote(self, obs):
        r = obs.round
        s_hat = self._estimate_S(obs)

        if "FORESIGHT" in obs.powers_theirs:
            # They may see up to 16 of OUR coins right now -- a real edge the
            # obligation formula doesn't price. Buy insurance with width.
            width = self.config.spread_cap(r)
        else:
            unseen_true = None
            if obs.foresight:
                base_unseen = self.config.N_COINS - self.config.REVEAL_PER_ROUND * r
                unseen_true = max(0, base_unseen - len(obs.foresight))
            width = self._best_width(r, unseen_true)

        lo = round(s_hat) - width // 2
        return (lo, lo + width)

    # ---------------- respond() ----------------

    def respond(self, obs, quote, turn):
        self._record_maker_quote(obs, quote)
        s_hat = self._estimate_S(obs)
        bid, ask = quote

        edge_buy = s_hat - ask
        edge_sell = bid - s_hat
        thresh = -1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0

        if turn == obs.n_turns:
            # Countering here forces the midpoint of OUR OWN counter and
            # makes us short + forcer, whoever we are. Price all three exits.
            fee = self.config.FORCED_FILL_FEE
            floor = obs.final_cap
            max_width = min(ask - bid, max(floor, (ask - bid) - self.config.MIN_REDUCTION))
            forced_bid, forced_ask = ask - max_width, ask   # pinned to ASK: forcing => short
            shift = 0
            for pw in ("TRICK_ROOM", "STEALTH_ROCK"):
                mag = self.config.POWERS[pw]["magnitude"]
                if pw in obs.powers_mine:
                    shift += mag
                if pw in obs.powers_theirs:
                    shift -= mag
            forced_price = (forced_bid + forced_ask) // 2 + shift
            edge_force = forced_price - s_hat - fee

            options = {"buy": edge_buy, "sell": edge_sell, "force": edge_force}
            best = max(options, key=options.get)
            if best == "buy":
                return "ACCEPT_BUY"
            if best == "sell":
                return "ACCEPT_SELL"
            return ("COUNTER", forced_bid, forced_ask)

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        w = max(0, (ask - bid) - self.config.MIN_REDUCTION)
        center = max(bid, min(round(s_hat), ask - w))
        return ("COUNTER", center, center + w)

    # ---------------- use_transform() ----------------

    def use_transform(self, obs):
        return abs(obs.k_mine) <= FLAT_THRESHOLD
