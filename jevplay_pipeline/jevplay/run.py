"""Frame-accurate offline recorder. Emulation is synchronous: while waiting on the model
no frames are produced, so model latency never distorts game timing (it is only displayed)."""
from __future__ import annotations
import argparse, json, subprocess, time, pathlib, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from .games import ADAPTERS
from .policies import JevPolicy, RulePolicy

W, H, FPS = 1920, 1080, 60
FD = "/usr/share/fonts/truetype/dejavu/DejaVuSans"
BG, CARD, LINE = (13, 15, 20), (17, 17, 17), (40, 44, 52)
TXT, DIM, CORAL = (235, 237, 240), (140, 146, 158), (240, 110, 95)


def font(sz, bold=False, mono=False):
    try:
        return ImageFont.truetype(FD + ("Mono" if mono else "") + ("-Bold" if bold else "") + ".ttf", sz)
    except OSError:
        return ImageFont.load_default()


F_TITLE, F_ACT, F_MED, F_SM, F_XS, F_MONO = font(34), font(44), font(28), font(22), font(19), font(17, True, True)
GX0, GY0, GX1, GY1 = 26, 92, 1280, 1062          # game card
SX = 1316                                         # sidebar x
GW, GH = 1024, 960                                # NES 256x240 at 4x (horizontal) / 4x


def render(game, frame, dec, idx, reward_total, status, cfg):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # top bar
    d.text((26, 26), cfg.get("title") or game.title, font=F_TITLE, fill=TXT)
    col, label = {"live": ((80, 210, 140), "Live decision loop"), "wait": ((240, 200, 90), "Waiting for Jev"),
                  "end": (CORAL, "Run ended")}[status]
    d.ellipse([1130, 36, 1142, 48], fill=col); d.text((1152, 30), label, font=F_XS, fill=DIM)
    d.rounded_rectangle([1740, 14, 1894, 66], 6, outline=(90, 96, 108), width=2)
    d.text((1772, 26), "Restart", font=F_SM, fill=TXT)
    d.line([26, 78, 1894, 78], fill=LINE)
    # game card with dark-grey side bars
    d.rounded_rectangle([GX0, GY0, GX1, GY1], 8, fill=(30, 33, 40), outline=LINE)
    gx = GX0 + ((GX1 - GX0) - GW) // 2; gy = GY0 + ((GY1 - GY0) - GH) // 2
    img.paste(Image.fromarray(frame).resize((GW, GH), Image.NEAREST), (gx, gy))
    # sidebar
    x, R = SX, 1894
    d.text((x, 96), game.level, font=F_SM, fill=TXT)
    t = f"Decision {idx:04d}"; d.text((R - d.textlength(t, font=F_SM), 96), t, font=F_SM, fill=DIM)
    if dec is None:
        d.text((x, 140), "waiting for first decision...", font=F_MED, fill=DIM); return img
    d.text((x, 136), dec.action.replace("_", " "), font=F_ACT, fill=TXT)
    why = game.options.get(dec.action, "")
    d.text((x, 196), why[:62] + ("..." if len(why) > 62 else ""), font=F_XS, fill=DIM)
    d.line([x, 236, R, 236], fill=LINE)
    for i, (k, v) in enumerate([("Confidence", f"{dec.confidence*100:.0f}%"),
                                ("Latency", f"{dec.latency_ms:.0f} ms"),
                                ("Reward", "—" if reward_total is None else f"{reward_total:+.1f}")]):
        cx = x + i * 196
        d.text((cx, 252), k, font=F_XS, fill=DIM); d.text((cx, 280), v, font=F_MED, fill=TXT)
    d.line([x, 330, R, 330], fill=LINE)
    d.text((x, 344), "Controller probabilities", font=F_SM, fill=TXT)
    y = 384
    for name in game.options:
        p = dec.probabilities.get(name, 0.0); ch = name == dec.action
        d.text((x, y), name.replace("_", " "), font=F_XS, fill=TXT if ch else DIM)
        pt = f"{p*100:.1f}%"; d.text((R - d.textlength(pt, font=F_XS), y), pt, font=F_XS, fill=TXT if ch else DIM)
        th = 10 if ch else 6; by = y + 28
        d.rounded_rectangle([x, by, R, by + th], 3, fill=(36, 40, 48))
        if p > 0.004:
            d.rounded_rectangle([x, by, x + int((R - x) * p), by + th], 3, fill=(245, 245, 250) if ch else (95, 104, 120))
        y += 50 if len(game.options) <= 7 else 44
    d.line([x, y + 4, R, y + 4], fill=LINE); y += 18
    d.text((x, y), "Situation", font=F_SM, fill=TXT); y += 40
    for lab, v, hot in [(game.labels[0], dec.must_act, False), (game.labels[1], dec.danger, True)]:
        d.text((x, y), lab, font=F_XS, fill=DIM)
        vt = "—" if v is None else f"{v*100:.1f}%"; d.text((R - d.textlength(vt, font=F_XS), y), vt, font=F_XS, fill=DIM)
        by = y + 28; d.rounded_rectangle([x, by, R, by + 8], 3, fill=(36, 40, 48))
        if v:
            c = (CORAL if v > 0.9 else (225, 180, 110)) if hot else (110, 140, 190)
            d.rounded_rectangle([x, by, x + int((R - x) * v), by + 8], 3, fill=c)
        y += 52
    y += 6
    for line in game.raw_lines(dec._state):
        d.text((x, y), line, font=F_MONO, fill=(170, 176, 188)); y += 26
    d.text((x, 1040), "R restarts · Esc or Q quits", font=F_XS, fill=(100, 106, 118))
    return img


def _mux_audio(env, out, nframes):
    """Audio captured per emulated frame (game paused while Jev decides, so it stays in sync). NES runs at
    60.0988 fps vs 60 fps video: the WAV rate is set so the audio spans exactly the video length."""
    import wave
    a = np.concatenate(env.audio).astype(np.int16)
    rate = int(round(len(a) / (nframes / FPS)))
    wav = str(out) + ".wav"
    w = wave.open(wav, "wb"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate); w.writeframes(a.tobytes()); w.close()
    tmp = str(out) + ".tmp.mp4"
    subprocess.check_call(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-i", wav, "-map", "0:v", "-map", "1:a",
                           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", tmp])
    os.replace(tmp, out); os.remove(wav)


def _default_macro(game, action, fpd):
    b = game.buttons.get(action, 0)
    for k in range(fpd):
        yield 0 if (action in game.tap and k >= 2) else b


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(pathlib.Path(__file__).parents[1] / "config.json"))
    ap.add_argument("--game"); ap.add_argument("--rom"); ap.add_argument("--policy")
    ap.add_argument("--seconds", type=float); ap.add_argument("--out"); ap.add_argument("--title"); ap.add_argument("--model"); ap.add_argument("--image-px", dest="image_px", type=int)
    ap.add_argument("--frames-per-decision", dest="frames_per_decision", type=int)
    ap.add_argument("--no-video", action="store_true"); ap.add_argument("--boot-delay", dest="boot_delay", type=int); ap.add_argument("--stop-on-death", action="store_true")
    a = ap.parse_args(argv)
    cfg = json.loads(pathlib.Path(a.config).read_text())
    for k in ("game", "rom", "policy", "seconds", "out", "title", "model", "image_px", "frames_per_decision", "boot_delay"):
        if getattr(a, k) is not None:
            cfg[k] = getattr(a, k)
    game = ADAPTERS[cfg["game"]]()
    game.boot_delay = cfg.get("boot_delay", 0)
    rule = RulePolicy(game.heuristic)
    pol = JevPolicy(cfg.get("model", "jev-latest"), int(cfg.get("image_px", 512))) if cfg["policy"] == "jev" else rule
    fpd = int(cfg.get("frames_per_decision", 8))
    out = pathlib.Path(cfg["out"]); out.parent.mkdir(parents=True, exist_ok=True)
    log = open(out.with_suffix(".jsonl"), "w")
    if cfg.get("emulator", "retro") == "retro":
        from .retro_env import RetroNES
        env = RetroNES(cfg["rom"]); env.reset()
    else:
        from nes_py import NESEnv
        env = NESEnv(cfg["rom"]); env.reset()
    frame = game.boot(env)
    if hasattr(env, "audio"):
        env.audio.clear()  # video starts after boot
    class _Null:
        def write(self, b): pass
        def close(self): pass
    ff = subprocess.Popen(["true"], stdin=subprocess.PIPE) if a.no_video else subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                           "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                           "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    total = int(cfg["seconds"] * FPS)
    dec, idx, lat, errs, reward_total = None, 0, [], 0, None
    tin = tout = 0
    prev_btn = 0
    t0 = time.time()
    try:
        it = iter(())
        for f in range(total):
            try:
                btn = next(it)
            except StopIteration:
                state = game.read_state(env, frame)
                dec = pol.choose(state, game.current_options() if hasattr(game, 'current_options') else game.options, game.instructions, rule, getattr(game, 'situation', None), frame)  # emulator paused here
                dec._state = state
                idx += 1; lat.append(dec.latency_ms); errs += bool(dec.error); tin += dec.tokens_in; tout += dec.tokens_out
                log.write(json.dumps({"frame": f, "state": state, **{k: v for k, v in dec.__dict__.items() if k != "_state"}}, default=str) + "\n")
                it = game.macro(env, dec.action) if hasattr(game, "macro") else _default_macro(game, dec.action, fpd)
                btn = next(it, 0)
            frame, *_ = env.step(btn)
            rw = game.reward(env)
            if rw is not None:
                reward_total = (reward_total or 0.0) + rw
            ended = game.done(env)
            if not a.no_video:
                ff.stdin.write(render(game, frame, dec, idx, reward_total, "end" if ended else "live", cfg).tobytes())
            if a.stop_on_death and getattr(game, "died", lambda e: False)(env):
                ended = True
            if ended:
                cleared = getattr(game, "summary", lambda e: {})(env).get("clear")
                for _ in range(0 if a.no_video else (int(3.5 * FPS) if cleared else FPS)):
                    if cleared:  # play out the real flagpole slide / castle walk
                        frame, *_ = env.step(0); f += 1
                    ff.stdin.write(render(game, frame, dec, idx, reward_total, "end", cfg).tobytes())
                break
    finally:
        ff.stdin.close(); ff.wait(); env.close(); log.close()
        if not a.no_video and getattr(env, "audio", None):
            _mux_audio(env, out, f + 1)
        c = getattr(pol, "close", None); c and c()
    s = {"out": str(out), "decisions": idx, "errors": errs, "wall_s": round(time.time() - t0, 1), "tokens_in": tin, "tokens_out": tout, "video_s": round(f / FPS, 1), **getattr(game, "summary", lambda e: {})(env)}
    if lat:
        L = sorted(lat); s.update(lat_ms_median=round(L[len(L)//2]), lat_ms_p90=round(L[int(len(L)*.9)]),
                                  lat_ms_max=round(L[-1]))
    print(json.dumps(s))


if __name__ == "__main__":
    main()
