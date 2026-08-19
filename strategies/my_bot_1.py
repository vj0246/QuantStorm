# Name: Vivaan Jainn
# College: Dwarkadas Jivanlal College of Engineering
# Roll Number: 60004240203

"""
starter_bot.py — Divided Oracle: QuantStorm 2026, V1
=====================================================

STATUS: beats every provided baseline in backtesting.
  vs NaiveEV          +6.86 ticks/deal   (n=300, seed=3)
  vs Rational         +6.08 ticks/deal   (n=300, seed=3)
  vs AdaptiveBidder   +3.38 ticks/deal   (n=300, seed=3)   <- the strong reference bot
Re-run these yourself before you trust them on a different seed:
    python backtester.py --bot1 strategies/my_bot.py --bot2 strategies/adaptive_bidder.py --n_deals 300 --seed <n>
Confirmed identical under --isolate (no accidental cross-deal state).

WHAT THIS IS
------------
Everything except the memory layer below is strategies/adaptive_bidder.py,
copied on purpose: RULEBOOK.md SS12 says the shipped baselines are free to
reuse, constants included, and re-deriving an already-measured number from
scratch just burns clock without new information.
  - POWER_VALUES: calibrated tick value of each power, per round (measured
    by the problem-setters, not us).
  - SHADE = 0.60: first-price bid-shade fraction. Basin is broad (0.55-0.65
    all within noise of each other); only badly wrong values are punished.
  - Quote at obs.final_cap (tightest legal width) as Maker: an honest quote
    breaks even on the maker-obligation transfer at ANY width (RULEBOOK.md
    SS7.2), so width above the floor only ever pays WIDTH_PREMIUM for
    nothing. If you later want to widen deliberately — e.g. you suspect
    the opponent has a real information edge on you this round and want
    to be harder to profitably cross — game_config.py's straddle_prob(r, w)
    tells you exactly which widths are "free" (tie the straddle rate of a
    narrower one) vs which actually buy you a higher hit rate:
        w=4,5,6 tie in round 1 -> never quote 5 or 6, they cost premium
        for zero extra coverage. Recompute per round with:
            [(w, config.straddle_prob(r, w)) for w in range(final_cap, spread_cap+1)]
  - TRANSFORM: fire from a flat hand (|k_mine| <= 1), decline from a
    decisive one. DENIAL_WEIGHT = 0.0 — buying it purely to deny a
    decisive-looking opponent is the one number in the whole reference set
    the authors flag as unresolved on the current 24-TE spec (it was
    negative on the old 40-TE spec, but the swap-vs-budget arithmetic has
    since moved). Worth 20 minutes on the backtester before the deadline if
    you have them; shipped at the same conservative 0.0 in the meantime
    rather than guessing a number nobody has measured.

WHAT'S NEW: cross-round opponent memory
----------------------------------------
adaptive_bidder.py — like every provided baseline — only ever looks at
THIS round's obs.foresight and, as Taker, blends it with the opponent's
opening quote at a fixed 50/50, discarding both the instant the round
ends. Two things about that are fixable for free:

1. FORESIGHT in rounds 1-4 is not a sample. Magnitude is 16 and the
   opponent has only revealed 4*round coins by round 4 (<=16), so you see
   ALL of it — a complete, exact readout, not noisy evidence to be
   averaged against anything. A fixed 0.5/0.5 blend throws away half the
   value of a signal that has zero error.
2. Nothing carries the read forward. Win FORESIGHT in round 2, see their
   exact 8-coin sum — and by round 4 a bot that doesn't remember it is
   back to a flat prior, negotiating as if round 2 never happened,
   despite being explicitly allowed to remember anything within a deal
   (RULEBOOK.md SS8: "You MAY remember things WITHIN a deal").

_remember_opp() below keeps a single running best read instead: every
signal is tagged with the round it was captured in and how good it is
(exact leak > quote-derived anchor), and a later round's read always
supersedes an earlier one rather than being blended with it — because a
later round strictly dominates: it reflects more of the opponent's
revealed hand, full stop. This is what moved the needle in testing
(+3.38/deal against a bot that is otherwise byte-for-byte identical).

OPEN QUESTIONS — worth your remaining time before DENIAL_WEIGHT:
  - Deliberately COUNTERing on the final turn instead of accepting, when
    you hold TRICK_ROOM/STEALTH_ROCK and the shifted forced-fill price
    beats your best available accept — you pay the 2-tick forcing fee but
    may still come out ahead. Nobody in the reference set does this; none
    of the three ever chooses to force on purpose. Untested here — the
    shift magnitude (2-3 ticks) is close enough to the fee (2 ticks) that
    it needs a real backtest before it goes in, not a guess.
  - The anchor assumes the opponent centres honestly. RULEBOOK.md's own
    measurement says distortion doesn't pay against a face-value reader
    (+0.80 +/- 1.51 heavy compression, -3.4 +/- 3.2 mild — both inside
    noise), so this isn't chasing a live exploit, just noting the
    assumption.
"""

import random

# Calibrated tick value of each power, per round. From adaptive_bidder.py —
# see its docstring for how these were measured. Re-derive if the config
# changes (TE_BUDGET, slate mode, N_TURNS all feed into these numbers).
POWER_VALUES = {
    "FORESIGHT":    {1: 0.76, 2: 1.16, 3: 1.48, 4: 1.97, 5: 2.02},
    "TRICK_ROOM":   {1: 1.14, 2: 0.00, 3: 0.00, 4: 0.60, 5: 0.52},
    "SUBSTITUTE":   {1: 1.46, 2: 1.15, 3: 0.95, 4: 0.57, 5: 0.29},
    "STEALTH_ROCK": {1: 1.51, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.00},
    "TRANSFORM":    {1: 1.58, 2: 1.24, 3: 1.31, 4: 0.00, 5: 0.00},
}

SHADE = 0.60               # first-price bid shade; broad optimum, 0.55-0.65
FLAT_THRESHOLD = 1         # |k_mine| at/under this -> hand is worth trading away
OPP_FLAT_THRESHOLD = 2.0   # opponent read this flat -> denial becomes worth considering
DENIAL_WEIGHT = 0.0        # UNRESOLVED on this spec -- see docstring, re-measure if time allows


class Bot:
    name = "my_bot_1"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)
        self._anchor = {}       # round -> our read of the opponent's revealed sum from their quote
        self._best_opp = None   # (round, quality, value) -- best opponent-sum read so far this deal

    # ---------- shared pricing ----------

    def _remember_opp(self, obs, quote=None):
        """Update the single best read of the opponent's revealed sum.

        Every signal is (round, quality, value). A later round always
        supersedes an earlier one; at equal round, an exact FORESIGHT leak
        (quality 2) beats a round-5 sample (1) beats a quote-derived
        anchor (0). Nothing is blended by a fixed weight regardless of how
        stale or how noisy it is.
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
        # Off the calibrated surface entirely (spec changed) -> 0.5, not a
        # confident number: wrong-low costs an auction, wrong-high costs
        # the rest of the deal's budget.
        return POWER_VALUES.get(name, {}).get(obs.round, 0.5)

    def _transform_value(self, obs):
        """What winning TRANSFORM is worth to us right now, in ticks.

        Flat hand -> the swap itself. Decisive hand with no read on the
        opponent -> nothing (paying to deny a swap that probably isn't
        coming loses money). Decisive hand AND opponent reads flat too ->
        priced denial, at DENIAL_WEIGHT (currently 0 -- unresolved).
        """
        swap = self._power_value(obs, "TRANSFORM")
        if abs(obs.k_mine) <= FLAT_THRESHOLD:
            return swap
        opp = self._best_opp[2] if self._best_opp else None
        if opp is not None and abs(opp) <= OPP_FLAT_THRESHOLD:
            return swap * DENIAL_WEIGHT
        return 0.0

    # ---------- required interface ----------

    def bid(self, obs, offered):
        """Blind TE bid: shaded fraction of calibrated fair value, in TE."""
        if not offered or obs.te_mine <= 0:
            return {}
        out = {}
        for name in offered:
            v = self._transform_value(obs) if name == "TRANSFORM" else self._power_value(obs, name)
            if v <= 0:
                continue
            fair_te = v / self.config.TE_SALVAGE      # ticks -> TE at the salvage rate
            out[name] = max(0, min(int(fair_te * SHADE), obs.te_mine))
        return out

    def quote(self, obs):
        """Maker's opening quote: centred on our best estimate, tightest legal width."""
        v = round(self._value(obs))
        cap = obs.final_cap
        lo = v - cap // 2
        return (lo, lo + cap)

    def respond(self, obs, quote, turn):
        """Accept on a real edge, else counter toward our value estimate."""
        bid, ask = quote
        v = self._value(obs, quote)
        edge_buy = v - ask
        edge_sell = bid - v
        thresh = -1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0   # capped downside -> cross on a thinner edge

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        w = max(0, (ask - bid) - self.config.MIN_REDUCTION)
        center = max(bid, min(round(v), ask - w))
        return ("COUNTER", center, center + w)

    def use_transform(self, obs):
        """Fire from a flat hand, decline from a decisive one — the power
        is spent either way, which is what makes declining a defence."""
        return abs(obs.k_mine) <= FLAT_THRESHOLD
