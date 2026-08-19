# Name: Vivaan Jain
# College: Dwarkadas Jivanlal College of Engineering
# Roll Number: 60004240203
"""Comparison build: SIMPLE. Static POWER_VALUES (unscaled), always quote at
final_cap, cross-round opponent memory, width-floor-safe counters, Turn-6
forcing (final_cap-width, safe under the new width>=floor rule). No width
search, no forcing-rate scaling -- the minimum that was already
individually validated, nothing speculative on top."""
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
DENIAL_WEIGHT = 0.0
FORCE_MARGIN = 1.0


class Bot:
    name = "my_bot_4"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)

    def _opp_best_signal(self, obs):
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

    def _power_value(self, obs, name):
        return POWER_VALUES.get(name, {}).get(obs.round, 0.5)

    def _transform_value(self, obs):
        swap = self._power_value(obs, "TRANSFORM")
        if abs(obs.k_mine) <= FLAT_THRESHOLD:
            return swap
        sig = self._opp_best_signal(obs)
        if sig is not None and abs(sig[2]) <= OPP_FLAT_THRESHOLD:
            return swap * DENIAL_WEIGHT
        return 0.0

    def bid(self, obs, offered):
        if not offered or obs.te_mine <= 0:
            return {}
        raw = {}
        for name in offered:
            v = self._transform_value(obs) if name == "TRANSFORM" else self._power_value(obs, name)
            if v <= 0:
                continue
            fair_te = v / self.config.TE_SALVAGE
            raw[name] = max(0, int(fair_te * SHADE))
        total = sum(raw.values())
        if total > obs.te_mine:
            scale = obs.te_mine / total
            raw = {k: int(val * scale) for k, val in raw.items()}
        return raw

    def quote(self, obs):
        v = round(self._value(obs))
        cap = obs.final_cap
        lo = v - cap // 2
        return (lo, lo + cap)

    def respond(self, obs, quote, turn):
        bid, ask = quote
        v = self._value(obs, quote)
        edge_buy = v - ask
        edge_sell = bid - v
        thresh = -1.0 if "SUBSTITUTE" in obs.powers_mine else 0.0

        if turn >= self.config.N_TURNS:
            my_shift = sum(
                self.config.POWERS[n]["magnitude"]
                for n in ("TRICK_ROOM", "STEALTH_ROCK")
                if n in obs.powers_mine and n in self.config.POWERS
            )
            opp_shift = sum(
                self.config.POWERS[n]["magnitude"]
                for n in ("TRICK_ROOM", "STEALTH_ROCK")
                if n in obs.powers_theirs and n in self.config.POWERS
            )
            net_shift = my_shift - opp_shift   # fill_shift() nets opposing holders exactly (engine.py)
            fw = obs.final_cap
            mid = ask - fw / 2.0
            force_payoff = (mid - v) + net_shift - self.config.FORCED_FILL_FEE
            accept_payoff = max(edge_buy, edge_sell)
            if force_payoff > accept_payoff + FORCE_MARGIN:
                return ("COUNTER", ask - fw, ask)

        if edge_buy > thresh and edge_buy >= edge_sell:
            return "ACCEPT_BUY"
        if edge_sell > thresh:
            return "ACCEPT_SELL"

        w = max(obs.final_cap, (ask - bid) - self.config.MIN_REDUCTION)
        center = max(bid, min(round(v), ask - w))
        return ("COUNTER", center, center + w)

    def use_transform(self, obs):
        return abs(obs.k_mine) <= FLAT_THRESHOLD
