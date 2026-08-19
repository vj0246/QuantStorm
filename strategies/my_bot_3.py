# Name: Vivaan Jain
# College: Dwarkadas Jivanlal College of Engineering
# Roll Number: 60004240203

"""
my_bot.py -- Divided Oracle: V3
================================

V3 changes from V2, driven by one goal: don't be a fixed function an
opponent can reverse-engineer from a handful of observed bids/quotes.

  1. AUCTION SHADE IS A DISTRIBUTION, NOT A CONSTANT.
     First-price sealed-bid, own-bid-pays, against an opponent whose
     value distribution we don't know in closed form: the honest
     theoretical statement is "shade meaningfully below 1.0", not a
     single sharp number -- pinning an exact optimal shade requires
     assuming the opponent's bid distribution, which is circular
     (their optimal bid depends on ours). So: sample
     shade ~ Triangular(low=0.45, mode=0.60, high=0.75) independently
     per bid. Mode kept near the region self-play showed to be
     competitive (see measure_powers.py / shade_sweep.py in the repo
     this shipped from), used only to SANITY-CHECK the band is not
     broken -- not to hill-climb a single constant against one known
     opponent, since the tournament field is unpublished and fitting
     tightly to any one reference bot is the wrong target.

  2. NEGOTIATION HAS THRESHOLD JITTER + OCCASIONAL FIRM-COUNTER BLUFF.
     Previously: always accept iff edge > exactly 0 (or -1 with
     SUBSTITUTE), always counter to exactly round(v). Both are clean,
     stable signatures an adaptive opponent could read off after a
     few rounds. Now: the accept threshold gets small per-decision
     noise, and with modest probability we counter tighter (firmer)
     than our true v would justify -- a real, small-cost bluff that
     denies a repeat opponent a clean fix on our fair-value estimate,
     rather than decorative randomness layered on top of the EV-
     optimal choice.

  3. Everything else (pricing via _value(), power-value table,
     TRANSFORM logic, adverse-selection-style forcing-rate read) is
     unchanged from V2 -- those aren't opponent-facing repeated
     signals in the same way (a single quote per round, a power value
     per round, at most 1-3 TRANSFORM decisions a match), so
     randomizing them buys little and costs clarity.

No ML, no historical-data fitting: every constant here is either
derived from the auction-theory structure of the game (first-price,
own-bid-pays) or measured directly on this engine via controlled
self-play ablation, and is stated as a justified band, not a
curve-fit point estimate.
"""

import random


# ── Power tick-value surface, per round ────────────────────────────
# Self-derived via measure_powers.py: forced-win ablation (1-TE bid vs
# 0-TE bid, otherwise-identical honest-pricing bots, mirrored matches
# to cancel role bias, ground-truth occurrence count from the bot's
# own bid() calls). See that script for the full method.
POWER_VALUES = {
    "FORESIGHT":    {1: 0.53, 2: 0.88, 3: 1.02, 4: 1.50, 5: 1.29},
    "TRICK_ROOM":   {1: 1.14, 2: 0.67, 3: 0.00, 4: 0.00, 5: 0.46},
    "SUBSTITUTE":   {1: 1.44, 2: 1.13, 3: 0.93, 4: 0.68, 5: 0.65},
    "STEALTH_ROCK": {1: 1.59, 2: 0.80, 3: 0.33, 4: 0.34},
    "TRANSFORM":    {1: 1.30, 2: 1.75, 3: 1.07},
}

# Powers whose value depends on reaching a forced midpoint fill.
FILL_SHIFT_POWERS = {"TRICK_ROOM", "STEALTH_ROCK"}
FORCING_RATE_PRIOR = 0.17
PRIOR_STRENGTH = 3.0

#: Auction shade is sampled per-bid from a triangular distribution,
#: not fixed. Band deliberately narrow: ablation against AdaptiveBidder
#: showed cost climbs fast and immediately with band width (-0.7/match
#: at +/-0, -2.1 at +/-0.03, -3.3 at +/-0.10) -- there's no free width
#: here, every bit of spread trades measurable EV against a value-
#: bidding opponent for unpredictability. +/-0.04 keeps per-bid shade
#: visibly non-constant (defeats exact-value pattern matching) while
#: keeping the measured cost small. This is an explicit judgment call,
#: not a free win: widen it if you'd rather pay more for more spread,
#: narrow it toward 0 to recover the deterministic V2's better EV
#: against non-adaptive opponents.
SHADE_LOW, SHADE_MODE, SHADE_HIGH = 0.56, 0.60, 0.64

FLAT_THRESHOLD = 1
OPP_FLAT_THRESHOLD = 2.0
DENIAL_WEIGHT = 0.3

#: Negotiation: standard deviation of the per-decision threshold jitter
#: added to the accept/counter boundary, in ticks. Small relative to
#: typical spread widths (final_cap is 2-4) so it nudges the boundary
#: without materially hurting EV on any single decision.
THRESHOLD_JITTER_SD = 0.35

#: Probability of a firm-counter bluff on any given COUNTER turn: shrink
#: the width slightly more than the honest w would call for, without
#: moving off center. Costs a small amount of expected width-premium on
#: that turn; buys denial of a clean read on our true v over the match.
#: Lowered from an initial 0.15 for the same reason as the shade band --
#: against non-adaptive opponents this is close to pure cost, so keep
#: it low but nonzero (still breaks a perfect read on repeated play).
BLUFF_PROB = 0.08
BLUFF_EXTRA_SHRINK = 1


class Bot:
    name = "my_bot_3"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)
        self._opp_anchor = {}

    # ── pricing ──────────────────────────────────────────────────

    def _value(self, obs, quote=None):
        """S_hat: our own revealed sum + opponent read, blended with any
        FORESIGHT leak rather than added on top of it (both estimate the
        same underlying quantity once we're Taker with a leak -- adding
        raw double-counts and biases S_hat outward)."""
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
        n = len(obs.contracts)
        forced = sum(1 for c in obs.contracts if c.forced)
        return (forced + PRIOR_STRENGTH * FORCING_RATE_PRIOR) / (n + PRIOR_STRENGTH)

    def _power_value(self, obs, name):
        base = POWER_VALUES.get(name, {}).get(obs.round, 0.0)
        if name in FILL_SHIFT_POWERS and base > 0:
            rate = self._opponent_forcing_rate(obs)
            base *= rate / FORCING_RATE_PRIOR
        return base

    def _opponent_k(self, obs):
        earlier = [k for k in self._opp_anchor if k < obs.round]
        if not earlier:
            return None
        return self._opp_anchor[max(earlier)]

    def _transform_value(self, obs):
        swap = POWER_VALUES.get("TRANSFORM", {}).get(obs.round, 0.0)
        if abs(obs.k_mine) <= FLAT_THRESHOLD:
            return swap
        opp_k = self._opponent_k(obs)
        if opp_k is not None and abs(opp_k) <= OPP_FLAT_THRESHOLD:
            return swap * DENIAL_WEIGHT
        return 0.0

    def _sample_shade(self):
        return self.rng.triangular(SHADE_LOW, SHADE_HIGH, SHADE_MODE)

    def bid(self, obs, offered):
        if not offered or obs.te_mine <= 0:
            return {}

        out = {}
        for name in offered:
            v = self._transform_value(obs) if name == "TRANSFORM" else self._power_value(obs, name)
            if v <= 0:
                continue
            fair_te = v / self.config.TE_SALVAGE
            shade = self._sample_shade()
            bid_amount = max(0, min(int(fair_te * shade), obs.te_mine))
            if bid_amount > 0:
                out[name] = bid_amount
        return out

    # ── negotiation ──────────────────────────────────────────────

    def quote(self, obs):
        v = round(obs.k_mine + sum(obs.foresight))
        cap = obs.final_cap
        lo = v - cap // 2
        return (lo, lo + cap)

    def respond(self, obs, quote, turn):
        bid, ask = quote
        v = self._value(obs, quote)

        edge_buy = v - ask
        edge_sell = bid - v

        thresh = 0.0
        if "SUBSTITUTE" in obs.powers_mine:
            thresh -= 1.0
        # Small per-decision jitter on the accept boundary so the
        # accept/counter line isn't a perfectly fixed function of edge.
        thresh += self.rng.gauss(0.0, THRESHOLD_JITTER_SD)

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        w = max(0, (ask - bid) - self.config.MIN_REDUCTION)
        if self.rng.random() < BLUFF_PROB:
            # Firm-counter bluff: shrink a little more than the honest
            # width would call for, still centred on our true v. Denies
            # a repeat opponent a clean read of our fair value from
            # width alone; small, bounded cost since center is unmoved.
            w = max(0, w - BLUFF_EXTRA_SHRINK)
        center = max(bid, min(round(v), ask - w))
        return ("COUNTER", center, center + w)

    def use_transform(self, obs):
        return abs(obs.k_mine) <= FLAT_THRESHOLD
