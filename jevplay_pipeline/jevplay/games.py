"""Game adapters. Each adapter: options (name->desc), buttons per option,
read_state(env) -> dict for the model, heuristic(state, opts) -> scores,
boot(env) to skip title screens, done(env)."""
from __future__ import annotations
import numpy as np

# NES button bitmask (nes-py): A=1 B=2 Select=4 Start=8 Up=16 Down=32 Left=64 Right=128
A, B, SEL, START, UP, DOWN, LEFT, RIGHT = 1, 2, 4, 8, 16, 32, 64, 128


class Thwaite:
    """Thwaite (GPL-3.0 homebrew, Damian Yerrick). Missile-command style."""
    name = "Thwaite (homebrew, GPL-3.0)"
    instructions = {
        "question": "Which controller input should the player commit to next?",
        "goal": "Defend the town: move the crosshair onto incoming missiles' future position and fire.",
        "timing": "The chosen input is held for the next decision window (frames_per_decision frames).",
        "tips": "Fire when the nearest missile's projected position is within ~12px of the crosshair. "
                "Use fire_left if the target is on the left half (x<128), else fire_right.",
    }
    options = {
        "up": "Move crosshair up", "down": "Move crosshair down",
        "left": "Move crosshair left", "right": "Move crosshair right",
        "up_left": "Move crosshair up and left", "up_right": "Move crosshair up and right",
        "fire_left": "Fire an anti-missile from the left silo at the crosshair",
        "fire_right": "Fire an anti-missile from the right silo at the crosshair",
        "wait": "Do nothing",
    }
    buttons = {"up": UP, "down": DOWN, "left": LEFT, "right": RIGHT, "up_left": UP | LEFT,
               "up_right": UP | RIGHT, "fire_left": A, "fire_right": B, "wait": 0}
    tap = {"fire_left", "fire_right"}  # press once, then release

    def __init__(self):
        self.prev = []

    def boot(self, env):
        frame = None
        for i in range(700):
            frame, *_ = env.step(START if i % 60 < 4 else 0)
        return frame

    def read_state(self, env, frame):
        f = frame.astype(int)
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        # crosshair: saturated blue
        cm = (b > 180) & (r < 90) & (g < 150)
        ys, xs = np.nonzero(cm[:176])
        cross = {"x": int(xs.mean()), "y": int(ys.mean())} if len(xs) else None
        # missile heads: bright warm pixels in sky band
        mm = (r > 180) & (b < 120) & (g > 60)
        mm[176:] = False
        missiles = []
        ys, xs = np.nonzero(mm)
        if len(xs):
            pts = sorted(zip(xs.tolist(), ys.tolist()), key=lambda p: -p[1])
            for x, y in pts:
                if all(abs(x - m["x"]) > 10 or abs(y - m["y"]) > 10 for m in missiles):
                    missiles.append({"x": x, "y": y})
                if len(missiles) >= 4:
                    break
        for m in missiles:  # velocity from previous decision
            near = min(self.prev, key=lambda p: abs(p["x"] - m["x"]) + abs(p["y"] - m["y"]), default=None)
            if near and abs(near["x"] - m["x"]) + abs(near["y"] - m["y"]) < 30:
                m["dx"], m["dy"] = m["x"] - near["x"], m["y"] - near["y"]
            else:
                m["dx"] = m["dy"] = 0
        self.prev = [dict(m) for m in missiles]
        if cross and missiles:
            t = min(missiles, key=lambda m: -m["y"])
            tx, ty = t["x"] + t["dx"], t["y"] + t["dy"]
            rel = {"target_dx": tx - cross["x"], "target_dy": ty - cross["y"]}
        else:
            rel = {}
        return {"crosshair": cross, "incoming_missiles": missiles,
                "nearest_target_relative_to_crosshair": rel,
                "note": "screen 256x240, y grows downward, town at y~190; dx/dy = pixels moved per decision"}

    def heuristic(self, s, opts):
        sc = {o: 0.05 for o in opts}
        rel = s.get("nearest_target_relative_to_crosshair") or {}
        if not rel:
            sc["wait"] += 1
            return sc
        dx, dy = rel["target_dx"], rel["target_dy"]
        if abs(dx) < 12 and abs(dy) < 12:
            sc["fire_left" if (s["crosshair"]["x"] < 128) else "fire_right"] += 2
        elif dy < -10 and dx < -10: sc["up_left"] += 1
        elif dy < -10 and dx > 10: sc["up_right"] += 1
        elif abs(dx) >= abs(dy): sc["left" if dx < 0 else "right"] += 1
        else: sc["up" if dy < 0 else "down"] += 1
        return sc

    def done(self, env):
        return False


class SuperMarioBros:
    """Original SMB (user-supplied ROM). Reuses typesafe-mario's state parser and prompt.
    UNTESTED here: no legal ROM on the box yet."""
    name = "Super Mario Bros."
    tap = set()

    def __init__(self):
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "typesafe-mario/src"))
        from typesafe_mario.actions import ACTION_DESCRIPTIONS, Action
        from typesafe_mario.state import MarioStateParser
        self.Action = Action
        self.options = {a.value: ACTION_DESCRIPTIONS[a] for a in Action}
        # SIMPLE_MOVEMENT equivalents
        self.buttons = {"noop": 0, "right": RIGHT, "right_jump": RIGHT | A, "right_run": RIGHT | B,
                        "right_run_jump": RIGHT | A | B, "jump": A, "left": LEFT}
        self.instructions = {"question": "Which controller macro should Mario commit to next?",
                             "goal": "Advance toward the stage flag while avoiding death.",
                             "timing": "The selected action is held for the decision window."}
        self.parser = MarioStateParser(decision_horizon_frames=8)
        self.last_action = None

    def boot(self, env):
        frame = None
        for i in range(200):
            frame, *_ = env.step(START if i % 40 < 3 else 0)
        while env.ram[0x0770] != 1 or env.ram[0x0E] != 8:  # wait for gameplay
            frame, *_ = env.step(0)
        return frame

    @staticmethod
    def _digits(ram, a, n):
        return int("".join(str(d) for d in ram[a:a + n]))

    def info(self, ram):
        st = int(ram[0x0E]); yv = int(ram[0x00B5])
        x = int(ram[0x6D]) * 256 + int(ram[0x86])
        return {"world": int(ram[0x75F]) + 1, "stage": int(ram[0x75C]) + 1, "area": int(ram[0x760]) + 1,
                "x_pos": x, "y_pixel": int(ram[0x3B8]), "y_pos": 255 - int(ram[0x3B8]),
                "progress": x, "status": {0: "small", 1: "tall"}.get(int(ram[0x756]), "fireball"),
                "player_state": st, "life": int(ram[0x75A]), "time": self._digits(ram, 0x7F8, 3),
                "death": st in (0x06, 0x0B) or yv > 1,
                "clear": int(ram[0x0770]) == 2 or st in (0x04, 0x05)}

    def read_state(self, env, frame):
        snap = self.parser.parse(self.info(env.ram), env.ram, previous_action=self.last_action)
        self._snap = snap
        return snap.to_state()

    def heuristic(self, s, opts):
        sc = {o: 0.05 for o in opts}
        sc["right_run_jump" if getattr(self._snap, "stalled_steps", 0) >= 2 else "right_run"] += 1
        return sc

    def done(self, env):
        i = self.info(env.ram)
        return i["clear"] or (i["death"] and i["life"] == 0)


ADAPTERS = {"thwaite": Thwaite, "smb": SuperMarioBros}


# ---- presentation / situation metadata ----
def _repo_prompts():
    """Pull the exact Choice/Noul/Score prompt texts from typesafe-mario's policy.py."""
    import ast, pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "typesafe-mario/src/typesafe_mario/policy.py").read_text()
    out = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("_Choice", "_Noul", "_Score"):
            kw = {k.arg: k.value for k in node.keywords}
            try:
                out[node.func.attr] = {k: ast.literal_eval(v) for k, v in kw.items() if k != "criteria" or node.func.attr == "_Score"}
            except ValueError:
                pass
    return out


Thwaite.title = "Jev plays Thwaite"
Thwaite.level = "Day 1 (Sunday)"
Thwaite.labels = ("must fire now", "immediate danger")
Thwaite.situation = {
    "must_act": ("Should the player fire now? Use `nearest_target_relative_to_crosshair`.",
                 ("The projected target is within about 12 px of the crosshair", "The crosshair is not on any projected target")),
    "danger": ("Is the town in immediate danger from `incoming_missiles`?",
               ("A missile is below y=150 and still falling toward the town", "No missile is close to the town")),
}
Thwaite.reward = lambda self, env: None


def _thw_raw(self, s):
    c = s.get("crosshair") or {}
    ms = s.get("incoming_missiles", [])
    return [f"Crosshair  {c.get('x','?')}, {c.get('y','?')}",
            f"Missiles   {len(ms)}  " + " ".join(f"({m['x']},{m['y']})" for m in ms[:3]),
            f"Target rel {s.get('nearest_target_relative_to_crosshair') or '-'}"]
Thwaite.raw_lines = _thw_raw

SuperMarioBros.title = "Jev plays Mario"
SuperMarioBros.labels = ("must jump now", "immediate danger")
_orig_init = SuperMarioBros.__init__


def _smb_init(self):
    _orig_init(self)
    p = _repo_prompts()
    self.instructions = dict(p["_Choice"]["instructions"])
    self.instructions["inputs"] = ("`frame` (when present) is the live NES screen: Mario, enemies, pipes, "
                                   "blocks and pits are visible in it. `ram_facts` are exact values read from game "
                                   "memory; trust them for positions, speeds and timing.")
    self.situation = {
        "must_act": (p["_Noul"]["instructions"],
                     ("A forward jump must begin or stay held now", "No jump is needed this decision")),
        "danger": ("Is Mario in immediate danger: projected enemy contact (`hazard`) or a pit/fall "
                   "(`terrain`, `trajectory`) within the reaction horizon?",
                   ("Collision, fall, or enemy contact is imminent unless Mario acts now",
                    "Open, safe movement for at least the next decision")),
    }
    self._lastx = None
SuperMarioBros.__init__ = _smb_init
SuperMarioBros.level = property(lambda self: "World {world}-{stage}".format(**self.info(self._env.ram)) if getattr(self, "_env", None) else "World 1-1")


def _smb_reward(self, env):  # gym-super-mario-bros style: x progress, -1/s clock, -15 death
    self._env = env
    i = self.info(env.ram)
    x = i["x_pos"]; r = 0 if self._lastx is None else max(-5, min(5, x - self._lastx))
    self._lastx = x
    return r - (15 if i["death"] else 0)
SuperMarioBros.reward = _smb_reward


def _smb_raw(self, s):
    sn = self._snap
    return [f"Position   {sn.x}, {getattr(sn, 'y', '?')}",
            f"Nearby     {', '.join(str(e) for e in (s.get('hazard', {}).get('upcoming_enemies') or [])[:2]) or '-'}"[:70],
            f"Terrain    {str(s.get('terrain', {}))[:60]}",
            f"Time       {s.get('episode', {}).get('clock', '?')}"]
SuperMarioBros.raw_lines = _smb_raw


def _smb_died(self, env):
    return self.info(env.ram)["death"]
SuperMarioBros.died = _smb_died


def _smb_summary(self, env):
    i = self.info(env.ram)
    return {"x": i["x_pos"], "clear": i["clear"], "lives": i["life"], "time": i["time"]}
SuperMarioBros.summary = _smb_summary

from .smb import SMB  # noqa: E402
ADAPTERS["smb"] = SMB
ADAPTERS["smb_v1"] = SuperMarioBros
from .contra import Contra  # noqa: E402
ADAPTERS["contra"] = Contra
from .battlecity import BattleCity  # noqa: E402
ADAPTERS["battlecity"] = BattleCity
