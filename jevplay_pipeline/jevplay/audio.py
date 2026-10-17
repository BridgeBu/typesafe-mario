"""Game audio for a recorded run: replay the exact per-frame NES inputs in stable-retro (FCEUmm core, has APU
audio), check sync against the video, write WAV and mux as AAC.
Usage (.venv-retro): python audio.py VIDEO.mp4 BUTTONS.json OUT.mp4"""
import json, subprocess, sys, wave
import numpy as np
import stable_retro as r

video, bj, out = sys.argv[1:4]
B = json.load(open(bj))
rom = B.get("rom", "/workspace/jev-plays/roms/super-mario-bros.nes")
em = r.RetroEmulator(rom)
ORDER = [2, 0, 4, 8, 16, 32, 64, 128, 1]  # retro NES mask order: B,(Y),SELECT,START,UP,DOWN,LEFT,RIGHT,A
f0, f1 = B["boot_frames"] + B["f0"], B["boot_frames"] + B.get("f1", 10**9)
chunks, screens = [], {}
for i, b in enumerate(B["buttons"]):
    em.set_button_mask(np.array([1 if (k and b & k) else 0 for k in ORDER], dtype=np.uint8), 0)
    em.step()
    if i >= f0:
        chunks.append(em.get_audio().copy())
        if (i - f0) % 120 == 0:
            screens[i - f0] = em.get_screen().copy()
n_video = len(B["buttons"]) - f0
audio = np.concatenate(chunks).astype(np.int16)
# fit audio exactly to the video duration (NES runs 60.0988 fps, video is 60 fps)
dur = int(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", video]).strip())
rate = int(round(len(audio) / (dur / 60.0)))
w = wave.open(out + ".wav", "wb"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate); w.writeframes(audio.tobytes()); w.close()
# sync check: compare retro screen with the game area of the video at sampled frames
bad = []
for fi, scr in screens.items():
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", video, "-vf",
        f"select=eq(n\\,{fi}),crop=1024:960:141:97,scale=256:240:flags=neighbor", "-vframes", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    if len(raw) < 256 * 240 * 3:
        continue
    v = np.frombuffer(raw, np.uint8).reshape(240, 256, 3)[8:232, 8:248].astype(int)
    diff = np.abs(v - scr.astype(int)).mean()
    if diff > 25:
        bad.append((fi, round(diff, 1)))
print("audio samples", len(audio), "wav rate", rate, "video frames", dur, "sync mismatches", bad)
subprocess.check_call(["ffmpeg", "-y", "-v", "error", "-i", video, "-i", out + ".wav", "-map", "0:v", "-map", "1:a",
                       "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-shortest", "-movflags", "+faststart", out])
