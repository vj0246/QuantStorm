# Name: Vivaan Jain
# College: Dwarkadas Jivanlal College of Engineering
# Roll Number: 60004240203

"""
my_bot.py -- Divided Oracle: V2
================================

Adds to V1's honest pricing layer:

  - bid(): values each power via a self-derived per-round tick-value
    table (measure_powers.py, forced-win ablation self-play, NOT
    copied from strategies/adaptive_bidder.py -- independently
    measured, landed in the same ballpark, which the rulebook says is
    expected when the arithmetic is right). Converts ticks -> TE via
    /TE_SALVAGE, shades the bid (see SHADE_SWEEP notes below).

  - TRICK_ROOM / STEALTH_ROCK get an extra multiplier from the
    opponent's OBSERVED forcing rate this deal (Beta-shrinkage toward
    a 17% prior, since these powers only pay off on a forced midpoint
    fill and that rate varies ~4x by opponent style -- measured
    11.8% vs AdaptiveBidder-style up to 53.8% vs Rational-style).

  - TRANSFORM: bid to fire from a flat hand at full value; bid to
    DENY only when we read the opponent as flat too, sized as a
    fraction of the swap's own value (DENIAL_WEIGHT, re-measured
    at TE_BUDGET=24 rather than trusting the stale 40-TE-spec
    zero from the reference bot).

  - use_transform(): unchanged from V1 (flat -> fire, decisive ->
    decline).

Everything numeric here (POWER_VALUES, SHADE, DENIAL_WEIGHT,
FORCING_RATE_PRIOR) was measured on this engine via measure_powers.py
and sweep scripts, not copied from the shipped reference strategies.
"""

import random


# ── Power tick-value surface, per round ────────────────────────────
# Self-derived via measure_powers.py: forced-win ablation (1-TE bid vs
# 0-TE bid, otherwise-identical honest-pricing bots, mirrored matches
# to cancel role bias, ground-truth occurrence count from the bot's
# own bid() calls). See that script for the full method.
#
# Missing cells (power not eligible that round) simply never get
# looked up -- offered filters to eligible powers already.
POWER_VALUES = {
    "FORESIGHT":    {1: 0.53, 2: 0.88, 3: 1.02, 4: 1.50, 5: 1.29},
    "TRICK_ROOM":   {1: 1.14, 2: 0.67, 3: 0.00, 4: 0.00, 5: 0.46},
    "SUBSTITUTE":   {1: 1.44, 2: 1.13, 3: 0.93, 4: 0.68, 5: 0.65},
    "STEALTH_ROCK": {1: 1.59, 2: 0.80, 3: 0.33, 4: 0.34},
    "TRANSFORM":    {1: 1.30, 2: 1.75, 3: 1.07},
}
# Measured rounds 3-4 slightly negative (-0.08); floored at 0 here --
# a power worth exactly nothing is a power we don't bid on, and a
# negative "value" isn't meaningful for a non-negative TE bid anyway.

# Powers whose value depends on reaching a forced midpoint fill. Their
# effective value scales with how often THIS opponent forces, not just
# the round -- see FORCING_RATE_PRIOR below.
FILL_SHIFT_POWERS = {"TRICK_ROOM", "STEALTH_ROCK"}

# Baseline forced-fill rate to assume before we have observed this
# opponent (measured: two honest accept-on-edge bots like this one
# converge to ~17% forced fills). Real opponents range 12%-54% by
# style, which is why this is a live estimate, not a constant.
FORCING_RATE_PRIOR = 0.17
# Shrinkage weight, in units of "prior contracts worth of evidence".
# At round 5 we have at most 4 real observations, so the prior needs
# real weight or 1-2 early forces (or non-forces) swing the estimate
# wildly. PRIOR_STRENGTH=3 means round-2's single observation moves
# the estimate about 1/4 of the way to 0 or 1; by round 5 with 4 obs
# it's roughly half-weighted on real data.
PRIOR_STRENGTH = 3.0

#: Fraction of fair value to bid (first-price, own-bid-pays).
#: Solved by sweep in this engine against adaptive_bidder specifically
#: (the only reference opponent that also bids) -- see shade_sweep.py.
#: Basin peaks sharply at 0.60; 0.55-0.65 all within ~1.5 ticks/match
#: of each other, meaningfully worse outside that band.
SHADE = 0.60

#: How flat OUR revealed sum has to be before the hand is worth
#: trading away via TRANSFORM.
FLAT_THRESHOLD = 1
#: How flat the OPPONENT has to look, by our read of their earlier
#: quote, before we pay to deny them the swap.
OPP_FLAT_THRESHOLD = 2.0
#: Denial value, as a multiple of the swap's own tick value. Swept at
#: TE_BUDGET=24 (transform_denial_sweep.py) rather than trusting the
#: reference bot's stale 40-TE-spec zero. Result: genuinely inconclusive
#: at this sample size -- across independent seed batches the spread
#: between denial=0.0, 0.5, and 1.0 (roughly +1.4 to +1.6/match) is
#: smaller than the batch-to-batch noise (~3.5 ticks/match spread).
#: Kept modest and positive: the mechanism is directionally real (a
#: correctly-read flat opponent IS worth denying something), but the
#: data here can't justify a sharp number. Revisit with a larger sweep
#: if time allows before submission.
DENIAL_WEIGHT = 0.3


class Bot:
    name = "my_bot"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)
        self._opp_anchor = {}

    # ── pricing ──────────────────────────────────────────────────

    def _value(self, obs, quote=None):
        """S_hat: our own revealed sum + opponent read, blended with any
        FORESIGHT leak rather than added on top of it.

        Both signals estimate the SAME quantity (the opponent's revealed
        sum) once we are Taker with a leak: the anchor is the Maker's own
        quote centred on their k_mine, and the leak is a direct sample of
        their revealed coins. Adding both raw double-counts overlapping
        information and biases S_hat outward; blending (equal weight,
        matching the reference bot's own approach) is correct here.
        """
        if quote is None or obs.is_maker:
            return float(obs.k_mine + sum(obs.foresight))

        r = obs.round
        if r not in self._opp_anchor:
            self._opp_anchor[r] = (quote[0] + quote[1]) / 2.0
        anchor = self._opp_anchor[r]
        if obs.foresight:
            anchor = 0.5 * anchor + 0.5 * sum(obs.foresight)
        return float(anchor + obs.k_mine)

    # ── auction ──────────────────────────────────────────────────

    def _opponent_forcing_rate(self, obs):
        """Shrunk estimate of how often this opponent forces a midpoint fill.

        Looks at every contract so far this deal (obs.contracts), counts
        forced ones, and blends toward FORCING_RATE_PRIOR by
        PRIOR_STRENGTH pseudo-observations. Round 1 has zero contracts
        and returns the prior exactly.
        """
        n = len(obs.contracts)
        forced = sum(1 for c in obs.contracts if c.forced)
        return (forced + PRIOR_STRENGTH * FORCING_RATE_PRIOR) / (n + PRIOR_STRENGTH)

    def _power_value(self, obs, name):
        """What `name` is worth in THIS round, in ticks, adjusted for
        this opponent's observed forcing tendency where relevant."""
        base = POWER_VALUES.get(name, {}).get(obs.round, 0.0)
        if name in FILL_SHIFT_POWERS and base > 0:
            rate = self._opponent_forcing_rate(obs)
            # Scale relative to the prior this table was measured at,
            # so a bot with average forcing behaviour reproduces the
            # measured value exactly (multiplier = 1.0).
            base *= rate / FORCING_RATE_PRIOR
        return base

    def _opponent_k(self, obs):
        """Estimate the opponent's revealed sum from their earlier quote.

        Only rounds where we were Taker carry a reading (an honest Maker
        centres its own quote on its own revealed sum). Returns None if
        we have not seen them quote yet.
        """
        earlier = [k for k in self._opp_anchor if k < obs.round]
        if not earlier:
            return None
        return self._opp_anchor[max(earlier)]

    def _transform_value(self, obs):
        """What winning TRANSFORM is worth to us right now, in ticks.

        flat hand           -> swap value, full
        decisive + no read  -> 0 (nothing to deny with confidence)
        decisive + opponent
        looks flat too      -> DENIAL_WEIGHT * swap value
        """
        swap = POWER_VALUES.get("TRANSFORM", {}).get(obs.round, 0.0)
        if abs(obs.k_mine) <= FLAT_THRESHOLD:
            return swap
        opp_k = self._opponent_k(obs)
        if opp_k is not None and abs(opp_k) <= OPP_FLAT_THRESHOLD:
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
            bid_amount = max(0, min(int(fair_te * SHADE), obs.te_mine))
            if bid_amount > 0:
                out[name] = bid_amount
        return out

    # ── negotiation ──────────────────────────────────────────────

    def quote(self, obs):
        v = round(obs.k_mine + sum(obs.foresight))
        cap = obs.final_cap  # tightest legal width: zero premium, max obligation payout
        lo = v - cap // 2
        return (lo, lo + cap)

    def respond(self, obs, quote, turn):
        bid, ask = quote
        v = self._value(obs, quote)

        edge_buy = v - ask
        edge_sell = bid - v

        thresh = 0.0
        if "SUBSTITUTE" in obs.powers_mine:
            # Downside capped at 2 ticks -- cross on a thinner edge.
            thresh -= 1.0

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        w = max(0, (ask - bid) - self.config.MIN_REDUCTION)
        center = max(bid, min(round(v), ask - w))
        return ("COUNTER", center, center + w)

    def use_transform(self, obs):
        return abs(obs.k_mine) <= FLAT_THRESHOLD
