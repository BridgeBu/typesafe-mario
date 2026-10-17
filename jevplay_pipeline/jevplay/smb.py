"""Super Mario Bros. adapter v2: Jev sees the live frame (image) + exact RAM facts.
Physics/geometry is computed here in code; Jev picks the controller macro.
RAM map: datacrystal SMB RAM map (same addresses gym-super-mario-bros uses)."""
from __future__ import annotations
from .games import A, B, START, LEFT, RIGHT

ENEMY = {0x00: "green_koopa", 0x01: "red_koopa", 0x02: "buzzy_beetle", 0x03: "red_koopa", 0x05: "hammer_bro",
         0x06: "goomba", 0x07: "blooper", 0x08: "bullet_bill", 0x0A: "cheep_cheep", 0x0D: "piranha_plant",
         0x0E: "cheep_cheep", 0x11: "lakitu", 0x12: "spiny", 0x2E: "powerup", 0x30: "flagpole_flag",
         0x31: "flagpole"}
NON_HAZARD = {0x2E, 0x30, 0x31, 0x35, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B, 0x2C}


def s8(v):
    return v - 256 if v > 127 else v


class SMB:
    title = "Jev plays Mario"
    labels = ("must jump now", "immediate danger")
    options = {
        "noop": "Release the controls and let current momentum continue.",
        "right": "Walk right at normal speed for a short moment.",
        "right_jump": "Jump forward at walking speed (code holds jump until the top of the arc).",
        "right_run": "Run right (B held) for a short moment without jumping.",
        "right_run_jump": "Running forward jump: the longest and highest jump (held until the top of the arc).",
        "jump": "Jump straight up in place (held until the top of the arc).",
        "left": "Move left for a short moment to back off from danger.",
    }
    BTN = {"noop": 0, "right": RIGHT, "right_jump": RIGHT | A, "right_run": RIGHT | B,
           "right_run_jump": RIGHT | A | B, "jump": A, "left": LEFT}
    instructions = {
        "question": "Which controller macro should Mario do next to keep progressing right toward the flagpole without dying?",
        "inputs": "`frame` is the live NES screen. `facts` are exact values read from game memory and geometry "
                  "computed in code (distances in pixels, one tile = 16 px, speeds in pixels per frame).",
        "rules": [
            "Run right whenever the way ahead is clear: progress matters and the clock is limited.",
            "Jump over obstacles (pipes, stairs, walls) and pits: start a running jump when `facts.ahead.obstacle` "
            "or `facts.ahead.pit` is within about 3 tiles. Taller obstacles and wider pits need right_run_jump.",
            "Enemies: jump when an enemy on your level will reach Mario soon (`frames_until_contact` small); "
            "landing on top of a goomba or koopa defeats it.",
            "While airborne a jump action keeps steering forward; noop or left mid-air shortens the jump.",
            "Each option carries an `emulator_preview`: the exact outcome from the real game engine. Never pick an "
            "option whose preview says MARIO DIES when another option is safe. Among safe options prefer the one "
            "that keeps running right fastest. `facts.if_mario_does_this` holds the same previews as numbers.",
            "Details: `facts.if_mario_does_this` holds exact emulator previews. For each jump action: whether Mario "
            "lands safely if he jumps right now. For running/standing: how many frames until Mario dies if he "
            "never jumps (null = safe for 1.5 s). Jumping too early can be as fatal as too late: when a jump "
            "preview does not land safely but running is still safe for a while, keep running and jump later.",
            "If `facts.progress.stalled_decisions` grows, Mario is blocked: jump.",
        ],
    }
    situation = {
        "must_act": ("Must Mario start a jump right now to avoid an obstacle, pit, or enemy ahead?",
                     ("An obstacle, pit edge, or enemy is close enough that waiting longer fails",
                      "The way ahead is clear for now or Mario is already airborne")),
        "danger": ("Is Mario in immediate danger of dying (enemy contact or falling into a pit) within the next half second?",
                   ("Contact with an enemy or a fall is imminent", "Mario is safe for at least the next half second")),
    }

    def __init__(self):
        self.level = "World 1-1"
        self._last_x, self.stalled, self.reward_x, self._env = None, 0, None, None

    # ---- emulator helpers ----
    def boot(self, env):
        frame = None
        for i in range(200):
            frame, *_ = env.step(START if i % 40 < 3 else 0)
        while env.ram[0x0770] != 1 or env.ram[0x0E] != 8:
            frame, *_ = env.step(0)
        for _ in range(int(getattr(self, "boot_delay", 0))):  # start-offset for robustness tests
            frame, *_ = env.step(0)
        return frame

    def info(self, r):
        st = int(r[0x0E])
        return {"x": int(r[0x6D]) * 256 + int(r[0x86]), "state": st,
                "death": st in (0x06, 0x0B) or int(r[0xB5]) > 1,
                "clear": st in (0x04, 0x05) or int(r[0x1D]) == 3,
                "life": int(r[0x75A]), "time": int("".join(str(int(d)) for d in r[0x7F8:0x7FB])),
                "world": int(r[0x75F]) + 1, "stage": int(r[0x75C]) + 1}

    def _solid(self, r, wx, sy):
        """Is the metatile at world-x wx, screen-y sy solid? (tile buffer 0x500, 2 pages x 13 rows x 16 cols)"""
        if sy < 32 or sy >= 240:
            return False
        page, col, row = (wx // 256) % 2, (wx % 256) // 16, (sy - 32) // 16
        t = int(r[0x500 + page * 208 + row * 16 + col])
        return t != 0 and t not in (0xC2, 0xC3)  # coins are not solid

    def read_state(self, env, frame):
        r = env.ram; self._env = env
        i = self.info(r)
        x, y = i["x"], int(r[0xCE])          # y = top of Mario sprite on screen (176 = standing on ground)
        on_ground = int(r[0x1D]) == 0
        feet = y + 16
        vx, vy = s8(int(r[0x57])) / 16.0, s8(int(r[0x9F]))  # px/frame (x is 4.4 fixed point)
        # terrain ahead, scanning 1..8 tiles in front (only within loaded buffer)
        obstacle = pit = None
        for k in range(1, 9):
            cx = x + 8 + k * 16
            if obstacle is None and self._solid(r, cx, feet - 8):
                h = 0
                while h < 8 and self._solid(r, cx, feet - 8 - h * 16):
                    h += 1
                obstacle = {"distance_px": k * 16 - 8, "height_tiles": h}
            if pit is None and not self._solid(r, cx, 208) and not self._solid(r, cx, 224):
                w = 0
                while w < 8 and not self._solid(r, cx + w * 16, 208) and not self._solid(r, cx + w * 16, 224):
                    w += 1
                pit = {"distance_px": k * 16 - 8, "width_tiles": w}
        enemies = []
        for s in range(5):
            if not r[0x0F + s]:
                continue
            t = int(r[0x16 + s])
            if t in NON_HAZARD:
                continue
            ex = int(r[0x6E + s]) * 256 + int(r[0x87 + s]); ey = int(r[0xCF + s])
            dx = ex - x
            if -32 < dx < 256:
                evx = s8(int(r[0x58 + s])) / 16.0 if s8(int(r[0x58 + s])) else 0.0
                close = vx - evx
                enemies.append({"_vx": evx, "kind": ENEMY.get(t, f"enemy_{t:02x}"), "dx_px": dx, "dy_px": ey - y,
                                "same_level": abs(ey - y) < 24,
                                "frames_until_contact": round((dx - 12) / close) if close > 0.2 and dx > 0 else None})
        enemies.sort(key=lambda e: abs(e["dx_px"]))
        lookahead = self._lookahead(env, x)
        for e in enemies:
            e.pop("_vx", None)
        self.stalled = self.stalled + 1 if (self._last_x is not None and x <= self._last_x) else 0
        self._last_x = x
        self.level = f"World {i['world']}-{i['stage']}"
        self._facts = {
            "mario": {"x": x, "screen_y": y, "on_ground": on_ground, "speed_x": round(vx, 2),
                      "speed_y": vy, "powerup": {0: "small", 1: "big"}.get(int(r[0x756]), "fire")},
            "ahead": {"obstacle": obstacle, "pit": pit},
            "if_mario_does_this": lookahead,
            "enemies": enemies[:3],
            "progress": {"x": x, "flagpole_x_approx": 3160, "time_left": i["time"], "stalled_decisions": self.stalled},
        }
        return {"facts": self._facts}

    def _score(self, env):
        return int("".join(str(int(d)) for d in env.ram[0x7DD:0x7E3]))

    def _jump_preview(self, env, action, x0, nested=False):
        """Exact emulator rollout: do the jump macro, keep the same direction until Mario lands."""
        if not nested:
            env._backup()
        try:
            sc0, n, died = self._score(env), 0, False
            for b in self.macro(env, action):
                env.step(b); n += 1
                if self.info(env.ram)["death"]:
                    died = True; break
            hold = self.BTN[action]  # same as the live loop repeating this action mid-air
            while not died and n < 120 and int(env.ram[0x1D]) != 0:
                env.step(hold); n += 1; died = self.info(env.ram)["death"]
            if not died:  # 30 frames after touching down, running on (catches corner slips and enemies)
                for _ in range(30):
                    env.step(self.BTN["right_run"]); n += 1
                    if self.info(env.ram)["death"]:
                        died = True; break
            return {"lands_safely": not died, "frames": n, "progress_px": self.info(env.ram)["x"] - x0,
                    "defeats_enemy": self._score(env) > sc0}
        finally:
            if not nested:
                env._restore()

    def _never_jump_preview(self, env, action, x0, frames=90):
        env._backup()
        try:
            for n in range(1, frames + 1):
                env.step(self.BTN[action])
                if self.info(env.ram)["death"]:
                    return {"dies_after_frames": n, "progress_px": self.info(env.ram)["x"] - x0}
            return {"dies_after_frames": None, "progress_px": self.info(env.ram)["x"] - x0}
        finally:
            env._restore()

    def _then_jump_preview(self, env, first, x0):
        """Do `first` for one decision window, then a running jump; report whether Mario survives the landing."""
        env._backup()
        try:
            for b in self.macro(env, first):
                env.step(b)
                if self.info(env.ram)["death"]:
                    return {"lands_safely": False}
            return self._jump_preview(env, "right_run_jump", x0, nested=True)
        finally:
            env._restore()

    @staticmethod
    def _jtxt(p):
        return (f"lands safely ({p['progress_px']:+d} px" + (", defeats an enemy" if p["defeats_enemy"] else "") + ")"
                if p["lands_safely"] else f"MARIO DIES before landing safely ({p['frames']} frames)")

    def _lookahead(self, env, x0):
        """Exact game-physics previews from an emulator save-state (code owns physics; Jev decides)."""
        pv = {a: self._jump_preview(env, a, x0) for a in ("right_run_jump", "right_jump", "jump")}
        nj = {a: self._never_jump_preview(env, a, x0) for a in ("right_run", "right", "noop", "left")}
        tj = {a: self._then_jump_preview(env, a, x0) for a in ("right_run", "right", "noop")}
        out = {}
        for a in pv:
            out[a] = "If chosen now: " + self._jtxt(pv[a])
        for a in nj:
            d = nj[a]["dies_after_frames"]
            t = ("safe for at least 1.5 s even without jumping" if d is None else
                 f"without any jump Mario dies in {d} frames")
            if a in tj:
                t += "; doing this now and a running jump next decision: " + (
                    "lands safely" if tj[a].get("lands_safely") else "MARIO DIES")
            out[a] = "If chosen now: " + t
        self._preview_text = out
        return {"jump_now": pv, "never_jump": nj, "this_then_run_jump": {k: v.get("lands_safely") for k, v in tj.items()}}

    def current_options(self):
        """Choice criteria with each option's exact preview attached (structured criteria, per docs)."""
        return {k: {"action": v, "emulator_preview": self._preview_text.get(k, "")} for k, v in self.options.items()}

    def macro(self, env, action):
        """Yield button bytes for one macro. Jumps are held to the arc top (code-owned physics)."""
        btn = self.BTN[action]
        r = env.ram
        if btn & A:
            if int(r[0x1D]) == 0:          # grounded: need an A edge, then hold until rising stops
                yield btn & ~A
                n = 0
                while n < 32:
                    yield btn; n += 1
                    if n > 4 and (s8(int(r[0x9F])) >= 0 or int(r[0x1D]) == 0):
                        break
            else:                           # airborne: keep steering with A held, re-decide on touchdown
                for _ in range(6):
                    yield btn
                    if int(r[0x1D]) == 0:
                        break
        else:
            for _ in range(8):
                yield btn

    def heuristic(self, s, opts):
        f = s["facts"]; sc = {o: 0.05 for o in opts}
        a = f["ahead"]; near = lambda o: o and o["distance_px"] < 40
        enemy = any(e["same_level"] and e["frames_until_contact"] is not None and e["frames_until_contact"] < 25 for e in f["enemies"])
        sc["right_run_jump" if (near(a["obstacle"]) or near(a["pit"]) or enemy or f["progress"]["stalled_decisions"] > 1) else "right_run"] += 1
        return sc

    def reward(self, env):
        x = self.info(env.ram)["x"]; d = 0 if self.reward_x is None else max(-5, min(5, x - self.reward_x))
        self.reward_x = x
        return d

    def done(self, env):
        i = self.info(env.ram)
        return i["clear"] or (i["death"] and i["life"] == 0)

    def died(self, env):
        return self.info(env.ram)["death"]

    def summary(self, env):
        i = self.info(env.ram)
        return {"x": i["x"], "clear": i["clear"], "lives": i["life"], "time": i["time"]}

    def raw_lines(self, s):
        f = s["facts"]; m = f["mario"]; e = f["enemies"]; a = f["ahead"]
        return [f"Position   {m['x']}, {m['screen_y']}   speed {m['speed_x']:+.1f}",
                f"Motion     {'grounded' if m['on_ground'] else 'airborne'}",
                f"Nearby     {', '.join(x['kind'] + ' ' + str(x['dx_px']) + 'px' for x in e[:2]) or 'none'}",
                f"Ahead      " + ("pit %dpx" % a['pit']['distance_px'] if a['pit'] else "no pit") + ", " +
                ("obstacle %dpx h%d" % (a['obstacle']['distance_px'], a['obstacle']['height_tiles']) if a['obstacle'] else "clear"),
                f"Time       {f['progress']['time_left']}"]
