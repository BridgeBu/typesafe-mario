"""Battle City adapter. RAM verified empirically on this ROM (stable-retro):
0x51 lives, 0x73..0x76 kills per enemy type (100/200/300/400 pts), 0x80 enemies left to spawn,
0x90+i / 0x98+i tank x / y (i=0 player, 2..7 enemies, 255 = empty), 0xA0+i tank status,
0x5A becomes 255 when the base (eagle) is destroyed."""
from __future__ import annotations
from .games import A, START, UP, DOWN, LEFT, RIGHT

PTS = (100, 200, 300, 400)


class BattleCity:
    title = "Jev plays Battle City"
    labels = ("must shoot now", "immediate danger")
    options = {
        "up_fire": "Drive up while firing.",
        "down_fire": "Drive down while firing.",
        "left_fire": "Drive left while firing.",
        "right_fire": "Drive right while firing.",
        "hold_fire": "Stay in place and keep firing in the current direction.",
    }
    BTN = {"up_fire": UP, "down_fire": DOWN, "left_fire": LEFT, "right_fire": RIGHT, "hold_fire": 0}
    instructions = {
        "question": "Which action should the player's tank take next to destroy enemy tanks while protecting "
                    "the base (eagle at the bottom center) and staying alive?",
        "inputs": "`frame` is the live NES screen. `ram_facts` are exact memory values (screen pixels).",
        "rules": [
            "Each option carries an `emulator_preview`: the exact outcome from the real game engine for the "
            "next ~1.5 s. Never pick an option whose preview says the tank is DESTROYED or the BASE FALLS when "
            "another option is safe.",
            "Among safe options prefer the one that destroys enemy tanks (points) soonest.",
        ],
    }
    situation = {
        "must_act": ("Is an enemy tank lined up with the player's tank so that firing now would hit it?",
                     ("An enemy is in the line of fire right now", "No enemy is in the line of fire")),
        "danger": ("Is the player's tank or the base in immediate danger of being hit?",
                   ("A hit on the player or the base is imminent", "Safe for at least the next second")),
    }

    def __init__(self):
        self.level = "Stage 1"
        self.reward_x = None

    def boot(self, env):
        frame = None
        for i in range(700):
            frame, *_ = env.step(START if i in (120, 121, 200, 201, 320, 321) else 0)
        for _ in range(int(getattr(self, "boot_delay", 0))):
            frame, *_ = env.step(0)
        return frame

    def info(self, r):
        kills = [int(r[0x73 + k]) for k in range(4)]
        return {"lives": int(r[0x51]), "kills": sum(kills), "score": sum(k * p for k, p in zip(kills, PTS)),
                "base_destroyed": int(r[0x5A]) == 255, "left_to_spawn": int(r[0x80]),
                "x": int(r[0x90]), "y": int(r[0x98])}

    def macro(self, env, action):
        btn = self.BTN[action]
        for k in range(8):
            yield btn | (A if k % 4 < 2 else 0)

    def _preview(self, env, first, then, frames=90):
        env._backup()
        try:
            i0 = self.info(env.ram); n = 0
            for act in [first] + [then] * 20:
                for b in self.macro(env, act):
                    env.step(b); n += 1
                    i1 = self.info(env.ram)
                    if i1["base_destroyed"] or i1["lives"] < i0["lives"]:
                        return {"bad": "BASE FALLS" if i1["base_destroyed"] else "TANK DESTROYED", "frames": n,
                                "points": i1["score"] - i0["score"]}
                    if n >= frames:
                        break
                if n >= frames:
                    break
            i1 = self.info(env.ram)
            return {"bad": None, "frames": n, "points": i1["score"] - i0["score"]}
        finally:
            env._restore()

    def read_state(self, env, frame):
        r = env.ram; i = self.info(r)
        enemies = []
        for s in range(2, 8):
            ex, ey = int(r[0x90 + s]), int(r[0x98 + s])
            if ex != 255 and ey != 255:
                enemies.append({"x": ex, "y": ey, "dx": ex - i["x"], "dy": ey - i["y"]})
        enemies.sort(key=lambda e: abs(e["dx"]) + abs(e["dy"]))
        pv = {a: {"keep_doing_this": self._preview(env, a, a), "then_hold_fire": self._preview(env, a, "hold_fire")}
              for a in self.options}
        self._pv = pv
        self._facts = {"player": {"x": i["x"], "y": i["y"], "lives": i["lives"]},
                       "base": {"x": 120, "y": 216, "destroyed": i["base_destroyed"]},
                       "enemies_on_field": enemies, "enemies_left_to_spawn": i["left_to_spawn"],
                       "score": i["score"], "kills": i["kills"], "previews": pv}
        return {"facts": self._facts}

    def current_options(self):
        txt = lambda p: (f"{p['bad']} after {p['frames']} frames" if p["bad"] else
                         (f"safe, destroys enemies (+{p['points']} pts)" if p["points"] else "safe, no kill"))
        return {a: {"action": d, "emulator_preview": "doing this for 1.5 s: " + txt(self._pv[a]["keep_doing_this"]) +
                    "; doing this once then holding fire: " + txt(self._pv[a]["then_hold_fire"])}
                for a, d in self.options.items()}

    def heuristic(self, s, opts):
        pv = s["facts"]["previews"]; sc = {o: 0.05 for o in opts}
        best = max(opts, key=lambda a: (pv[a]["keep_doing_this"]["bad"] is None, pv[a]["keep_doing_this"]["points"]))
        sc[best] += 1
        return sc

    def reward(self, env):
        sc = self.info(env.ram)["score"]; d = 0 if self.reward_x is None else max(0, sc - self.reward_x)
        self.reward_x = sc
        return float(d)

    def done(self, env):
        i = self.info(env.ram)
        return i["base_destroyed"] or i["lives"] == 0 or (i["left_to_spawn"] == 0 and not any(
            int(env.ram[0x90 + s]) != 255 for s in range(2, 8)))

    def died(self, env):
        i = self.info(env.ram)
        lost = getattr(self, "_lives0", None) is not None and i["lives"] < self._lives0
        if getattr(self, "_lives0", None) is None:
            self._lives0 = i["lives"]
        return i["base_destroyed"] or lost

    def summary(self, env):
        return self.info(env.ram)

    def raw_lines(self, s):
        f = s["facts"]; p = f["player"]; e = f["enemies_on_field"]
        return [f"Tank       {p['x']}, {p['y']}   lives {p['lives']}",
                f"Enemies    {len(e)} on field, {f['enemies_left_to_spawn']} to come",
                f"Nearest    " + (f"dx {e[0]['dx']:+d} dy {e[0]['dy']:+d}" if e else "none"),
                f"Kills      {f['kills']}   score {f['score']}"]
