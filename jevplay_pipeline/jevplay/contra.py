"""Contra adapter: Jev sees the live frame + exact RAM facts + exact emulator previews per option.
RAM (gym-contra / datacrystal): 0x2C screen type (4 = gameplay), 0x32 lives, 0x90 player state
(0 dead, 1 alive, 2 dying), 0x334 player x, 0x31A player y, 0x64/0x65 level scroll, 0x7E0.. score."""
from __future__ import annotations
from .games import A, B, START, UP, DOWN, LEFT, RIGHT


class Contra:
    title = "Jev plays Contra"
    labels = ("must dodge now", "immediate danger")
    options = {
        "run_right_fire": "Run right while firing forward.",
        "jump_right_fire": "Somersault jump forward while firing (dodges bullets and low enemies).",
        "prone_fire": "Lie flat and fire forward: ducks under bullets at standing height.",
        "aim_up_right_fire": "Run right firing diagonally up-right at targets above.",
        "aim_down_right_fire": "Run right firing diagonally down-right at targets below.",
        "stand_fire": "Stand still and fire forward.",
        "run_left_fire": "Back off to the left while firing.",
        "drop_down": "Drop down through the ledge you are standing on (down + jump) to a lower level.",
    }
    BTN = {"run_right_fire": RIGHT, "jump_right_fire": RIGHT | A, "prone_fire": DOWN,
           "aim_up_right_fire": UP | RIGHT, "aim_down_right_fire": DOWN | RIGHT, "stand_fire": 0,
           "run_left_fire": LEFT, "drop_down": DOWN | A}
    instructions = {
        "question": "Which controller action should the commando take next to push right through the stage, "
                    "destroying enemies, without getting hit?",
        "inputs": "`frame` is the live NES screen (enemies, bullets, turrets, platforms). `ram_facts` are exact "
                  "memory values; one hit kills the player.",
        "rules": [
            "Each option carries an `emulator_preview`: the exact outcome from the real game engine for the next "
            "~2 s. Never pick an option whose preview says the PLAYER DIES when another option survives.",
            "Among surviving options prefer the one that progresses right and destroys enemies (score gain).",
        ],
    }
    situation = {
        "must_act": ("Must the player dodge right now (jump or go prone) to avoid an incoming bullet or enemy?",
                     ("A bullet or enemy will hit the player unless he dodges now",
                      "No immediate threat that requires a dodge")),
        "danger": ("Is the player in immediate danger of being hit within the next half second?",
                   ("A hit is imminent", "Safe for at least the next half second")),
    }

    def __init__(self):
        self.level = "Stage 1 - Jungle"
        self._fire = 0
        self.reward_x = None

    def boot(self, env):
        frame = None
        for i in range(2000):
            frame, *_ = env.step(START if i % 30 < 2 else 0)
            if env.ram[0x2C] == 4 and i > 100:
                break
        self._lvl0 = int(env.ram[0x30])
        while env.ram[0x90] != 1:
            frame, *_ = env.step(0)
        for _ in range(int(getattr(self, "boot_delay", 0))):
            frame, *_ = env.step(0)
        return frame

    def _score(self, r):
        # verified empirically: 0x7E2/0x7E3 = P1 score / 100, little-endian (+1 per 100-pt soldier)
        return (int(r[0x07E2]) | (int(r[0x07E3]) << 8)) * 100

    def info(self, r):
        return {"progress": int(r[0x64]) * 256 + int(r[0x65]) + int(r[0x334]), "x": int(r[0x334]),
                "y": int(r[0x31A]), "state": int(r[0x90]), "lives": int(r[0x32]), "score": self._score(r),
                "dead": int(r[0x90]) != 1}

    def macro(self, env, action):
        btn = self.BTN[action]
        for k in range(8):
            fire = B if (k % 4) < 2 else 0          # rapid fire: B edge every 4 frames
            b = btn | fire
            if btn & A and k > 0:                    # jump: press A once, hold direction
                b = (btn & ~A) | fire
            yield b

    def _preview(self, env, first, then, frames=120):
        env._backup()
        try:
            i0 = self.info(env.ram); n = 0
            plan = [first] + [then] * 20
            for act in plan:
                for b in self.macro(env, act):
                    env.step(b); n += 1
                    if self.info(env.ram)["dead"]:
                        i1 = self.info(env.ram)
                        return {"dies": True, "frames": n, "progress_px": i1["progress"] - i0["progress"],
                                "score_gain": i1["score"] - i0["score"]}
                    if n >= frames:
                        break
                if n >= frames:
                    break
            i1 = self.info(env.ram)
            return {"dies": False, "frames": n, "progress_px": i1["progress"] - i0["progress"],
                    "score_gain": i1["score"] - i0["score"]}
        finally:
            env._restore()

    FOLLOW = ("run_right_fire", "jump_right_fire", "prone_fire", "stand_fire", "run_left_fire", "drop_down")

    @staticmethod
    def _rank(p):
        return (not p["dies"], p["frames"] if p["dies"] else 0, p["progress_px"] + p["score_gain"] / 10)

    def read_state(self, env, frame):
        r = env.ram; i = self.info(r)
        pv = {}
        for a in self.options:
            keep = self._preview(env, a, a)
            alts = {t: (keep if t == a else self._preview(env, a, t)) for t in self.FOLLOW}
            bt = max(alts, key=lambda t: self._rank(alts[t]))
            pv[a] = {"keep_doing_this": keep, "then_run_right": dict(alts[bt], then=bt)}
        self._pv = pv
        self._facts = {"player": {"screen_x": i["x"], "screen_y": i["y"], "lives": i["lives"]},
                       "progress": {"level_progress_px": i["progress"], "score_counter": i["score"]},
                       "previews": pv}
        return {"facts": self._facts}

    def current_options(self):
        out = {}
        for a, desc in self.options.items():
            k, t = self._pv[a]["keep_doing_this"], self._pv[a]["then_run_right"]
            txt = lambda p: ("PLAYER DIES after %d frames" % p["frames"]) if p["dies"] else \
                ("survives, %+d px, score +%d" % (p["progress_px"], p["score_gain"]))
            out[a] = {"action": desc, "emulator_preview": "doing this for 2 s: " + txt(k) +
                      f"; doing this once then the best follow-up ({t['then']}): " + txt(t)}
        return out

    def heuristic(self, s, opts):
        pv = s["facts"]["previews"]; sc = {o: 0.05 for o in opts}
        best = max(opts, key=lambda a: self._rank(pv[a]["then_run_right"]))
        sc[best] += 1
        return sc

    def reward(self, env):
        r = env.ram
        sc = self._score(r); d = 0 if self.reward_x is None else max(0, sc - self.reward_x)
        self.reward_x = sc
        return float(d)

    def done(self, env):
        return int(env.ram[0x38]) == 1 or int(env.ram[0x30]) != getattr(self, "_lvl0", 0)  # game over / stage clear

    def died(self, env):
        return self.info(env.ram)["dead"] and int(env.ram[0x30]) == getattr(self, "_lvl0", 0)

    def summary(self, env):
        i = self.info(env.ram)
        return {"progress": i["progress"], "lives": i["lives"], "score": i["score"], "dead": i["dead"]}

    def raw_lines(self, s):
        f = s["facts"]; p = f["player"]; pv = f["previews"]
        safe = [a for a in pv if not pv[a]["then_run_right"]["dies"]]
        return [f"Position   {p['screen_x']}, {p['screen_y']}",
                f"Progress   {f['progress']['level_progress_px']} px",
                f"Lives      {p['lives']}",
                f"Safe opts  {len(safe)}/{len(pv)}"]
