"""Zero-API clip renderer: deterministically replays a run log (same macros) and renders the overlay
for a frame window. Usage: python -m jevplay.clip LOG OUT START_S END_S [boot_delay]"""
import json, subprocess, sys
from nes_py import NESEnv
from . import games  # noqa
from .smb import SMB
from .policies import Decision
from .run import render, W, H, FPS


def main():
    log, out, s0, s1 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
    bd = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    L = [json.loads(l) for l in open(log)]
    g = SMB(); g.boot_delay = bd
    e = NESEnv("/workspace/jev-plays/roms/super-mario-bros.nes"); e.reset()
    BTNS, rec = [], [True]
    _step = e.step
    def step(b):
        if rec[0]:
            BTNS.append(int(b))
        return _step(b)
    e.step = step
    frame = g.boot(e)
    nboot = len(BTNS)
    _rs = g.read_state
    def read_state(env, fr):
        rec[0] = False
        try:
            return _rs(env, fr)
        finally:
            rec[0] = True
    g.read_state = read_state
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    f0, f1 = int(s0 * FPS), int(s1 * FPS)
    f, rt, keys = 0, 0.0, set(Decision.__dataclass_fields__)
    for i, d in enumerate(L):
        dec = Decision(**{k: v for k, v in d.items() if k in keys}); dec._state = d["state"]
        live = g.read_state(e, frame)  # identical emulator path to the live run (previews use save/restore)
        assert live["facts"]["mario"]["x"] == d["state"]["facts"]["mario"]["x"], ("replay diverged", i)
        for b in g.macro(e, d["action"]):
            frame, *_ = e.step(b); rt += g.reward(e)
            ended = g.died(e)
            if f0 <= f < f1:
                ff.stdin.write(render(g, frame, dec, i + 1, rt, "end" if ended else "live", {}).tobytes())
            f += 1
            if ended:
                for _ in range(FPS if f0 <= f <= f1 + 1 else 0):
                    ff.stdin.write(render(g, frame, dec, i + 1, rt, "end", {}).tobytes())
                ff.stdin.close(); ff.wait(); print(out, "ended at frame", f)
                json.dump({"boot_frames": nboot, "buttons": BTNS, "f0": f0, "f1": f1}, open(out + ".buttons.json", "w")); return
        if f >= f1:
            break
    while f < f1 and g.info(e.ram)["clear"] and not g.died(e):  # cleared: play out the flagpole slide
        frame, *_ = e.step(0); rt += g.reward(e)
        if f0 <= f: ff.stdin.write(render(g, frame, dec, len(L), rt, "end" if f > f1 - FPS else "live", {}).tobytes())
        f += 1
    while f < f1 and not g.died(e):  # log ended: Mario is mid-death-fall; keep last input
        frame, *_ = e.step(g.BTN[L[-1]["action"]])
        if f0 <= f: ff.stdin.write(render(g, frame, dec, len(L), rt, "end" if g.died(e) else "live", {}).tobytes())
        f += 1
    for _ in range(FPS if g.died(e) else 0):
        ff.stdin.write(render(g, frame, dec, len(L), rt, "end", {}).tobytes())
    print("x", g.info(e.ram)["x"], "died", g.died(e))
    ff.stdin.close(); ff.wait(); print(out, "frames", f0, f1)
    json.dump({"boot_frames": nboot, "buttons": BTNS, "f0": f0, "f1": f1}, open(out + ".buttons.json", "w"))


if __name__ == "__main__":
    main()
