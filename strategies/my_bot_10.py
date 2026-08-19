# Name: Vivaan Jain
# College: Dwarkadas J. Sanghvi College of Engineering
# Roll Number: 60004240203

import random
from math import comb


class Bot:
    name = "my_bot_10"

    def reset(self, seat, config, seed):
        self.seat = seat
        self.config = config
        self.rng = random.Random(seed)
        self.current_anchor = 0.0

    def bid(self, obs, offered):
        values = {
            "FORESIGHT":    {1: 0.76, 2: 1.16, 3: 1.48, 4: 1.97, 5: 2.02},
            "TRICK_ROOM":   {1: 1.125, 2: 0.125, 3: 0.125, 4: 0.625, 5: 0.375},
            "SUBSTITUTE":   {1: 1.46, 2: 1.15, 3: 0.95, 4: 0.57, 5: 0.29},
            "STEALTH_ROCK": {1: 1.51, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.00},
            "TRANSFORM":    {1: 1.58, 2: 1.24, 3: 1.31, 4: 0.00, 5: 0.00},
        }

        out = {}
        for power in offered:
            value = values.get(power, {}).get(obs.round, 0.0)

            if power == "TRANSFORM" and abs(obs.k_mine) > 1:
                value = 0.0

            if value > 0.0:
                # First-price shading. 0.64 is deliberately applied to the
                # reference fair-value surface, with integer TE resolution.
                bid = int((value / self.config.TE_SALVAGE) * 0.64)
                out[power] = min(obs.te_mine, bid)

        return out

    def quote(self, obs):
        value = obs.k_mine + sum(obs.foresight)

        # Floor + 1 is generally the best maker width in our sweep.
        # Round-5 Foresight has enough information to justify width 4.
        width = (
            4
            if obs.round == 5 and obs.foresight
            else min(obs.spread_cap, obs.final_cap + 1)
        )

        # Deliberate information distortion. This was selected by repeated
        # out-of-sample sweeps against a heterogeneous opponent pool.
        centre = round(value) - 2

        return (
            centre - width // 2,
            centre - width // 2 + width,
        )

    def respond(self, obs, quote, turn):
        bid, ask = quote
        value = obs.k_mine

        if not obs.is_maker:
            # The opening quote is the clean signal. Later ranges are
            # strategically contaminated by both players.
            if turn == 2:
                self.current_anchor = (bid + ask) / 2

            value = self._value(
                obs,
                self.current_anchor,
                self.current_anchor,
            )

        edge_buy = value - ask
        edge_sell = bid - value

        if value > ask:
            return "ACCEPT_BUY"

        if value < bid:
            return "ACCEPT_SELL"

        width = max(
            obs.final_cap,
            (ask - bid) - self.config.MIN_REDUCTION,
        )

        if turn == obs.n_turns:
            # On the final turn, countering makes us the short side.
            # Therefore, conditional on forcing, maximize the legal
            # forced midpoint rather than centering the range on value.
            my_shift = 0
            if "TRICK_ROOM" in obs.powers_mine:
                my_shift += int(self.config.POWERS["TRICK_ROOM"]["magnitude"])
            if "STEALTH_ROCK" in obs.powers_mine:
                my_shift += int(self.config.POWERS["STEALTH_ROCK"]["magnitude"])

            opp_shift = 0
            if "TRICK_ROOM" in obs.powers_theirs:
                opp_shift += int(self.config.POWERS["TRICK_ROOM"]["magnitude"])
            if "STEALTH_ROCK" in obs.powers_theirs:
                opp_shift += int(self.config.POWERS["STEALTH_ROCK"]["magnitude"])

            max_midpoint = ((ask - width) + ask) // 2 + my_shift - opp_shift
            edge_force = (
                max_midpoint
                - value
                - self.config.FORCED_FILL_FEE
            )

            if (
                edge_buy >= edge_sell
                and edge_buy >= edge_force
                and edge_buy >= 0
            ):
                return "ACCEPT_BUY"

            if edge_sell >= edge_force and edge_sell >= 0:
                return "ACCEPT_SELL"

            if edge_force >= max(edge_buy, edge_sell):
                return ("COUNTER", ask - width, ask)

            return "ACCEPT_BUY" if edge_buy >= edge_sell else "ACCEPT_SELL"

        centre = max(
            bid,
            min(round(value), ask - width),
        )

        return ("COUNTER", centre, centre + width)

    def _value(self, obs, bid, ask):
        q = (bid + ask) / 2

        if "FORESIGHT" in obs.powers_theirs:
            # Through round 4, Foresight exposes all currently revealed
            # opponent coins, so the opening midpoint is already the
            # posterior mean of the full score.
            if obs.round <= 4:
                return q

            # Round 5: the opponent sees 16 of our 20 revealed coins.
            # Compute the exact finite-population posterior over the
            # opponent's private 20-coin sum.
            kt = obs.k_mine

            positive = (20 + kt) // 2
            denominator = comb(20, 16)

            sample_prob = {}

            for j in range(
                max(0, 16 - (20 - positive)),
                min(16, positive) + 1,
            ):
                sample_sum = 2 * j - 16
                probability = (
                    comb(positive, j)
                    * comb(20 - positive, 16 - j)
                    / denominator
                )
                sample_prob[sample_sum] = probability

            numerator = 0.0
            denominator_post = 0.0

            for opponent_sum in range(-20, 21, 2):
                unseen_sample = q - opponent_sum
                probability = sample_prob.get(unseen_sample, 0.0)

                if probability:
                    denominator_post += probability
                    numerator += opponent_sum * probability

            if denominator_post:
                return kt + numerator / denominator_post

            return kt + q - 0.8 * kt

        # Without Foresight, the opening midpoint estimates the opponent's
        # revealed sum, so total-score posterior = own revealed + quote signal.
        return obs.k_mine + q

    def use_transform(self, obs):
        # TRANSFORM is most valuable when our revealed hand is nearly flat.
        return abs(obs.k_mine) <= 1
