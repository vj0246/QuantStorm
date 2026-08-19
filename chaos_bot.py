import random

class Bot:
    name = "Chaos"
    def reset(self, seat, config, seed):
        self.config = config
        self.rng = random.Random(seed)

    def bid(self, obs, offered):
        return {p: self.rng.randint(0, obs.te_mine) for p in offered}

    def quote(self, obs):
        center = self.rng.randint(-40, 40)
        w = self.rng.randint(obs.final_cap, obs.spread_cap)
        return (center - w//2, center - w//2 + w)

    def respond(self, obs, quote, turn):
        actions = ["ACCEPT_BUY", "ACCEPT_SELL", "COUNTER"]
        choice = self.rng.choice(actions)
        if choice == "COUNTER":
            center = self.rng.randint(-40, 40)
            w = self.rng.randint(obs.final_cap, quote[1] - quote[0])
            w = max(w, obs.final_cap)
            return ("COUNTER", center, center + w)
        return choice
        
    def use_transform(self, obs):
        return self.rng.choice([True, False])