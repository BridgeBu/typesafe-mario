# Jev plays (classic NES) — video pipeline

Code: `/workspace/jev-plays/pipeline/jevplay/` (also on branch `jev-plays` of https://github.com/BridgeBu/typesafe-mario)
Upstream base: github.com/fhshaik/typesafe-mario (its SMB state parser + prompts are reused verbatim for Mario).

## How it works
- Emulator: `nes-py` (headless, no X/xvfb needed). One emulator process.
- Every `frames_per_decision` frames (default 8 = ~7.5 decisions/s of game time) the game adapter
  builds a named-JSON state from RAM (Mario) or the frame (homebrew), and makes **one** Jev request
  (`model="jev-latest"`) with parallel questions over the same state:
  `Choice` over the action set (distribution -> probability bars, confidence -> readout),
  `Noul` "must jump/fire now", `Noul` "immediate danger" (bar turns coral when > 0.9).
- Emulation is synchronous, so the game is **paused** while waiting for Jev; real latency is
  measured and shown but never distorts game timing. Video is rendered offline at 60 fps,
  1920x1080 H.264 (ffmpeg libx264, yuv420p, faststart). Layout follows `/workspace/jev-ref/layout-spec.md`.
- On model error the step falls back to the rule AI and the overlay says so.
- Per-decision log next to the mp4 (`.jsonl`): state, probabilities, nouls, latency, tokens.

## Run
```bash
cd /workspace/jev-plays/pipeline
../.venv/bin/python -m jevplay.run                      # uses config.json
../.venv/bin/python -m jevplay.run --policy rule --seconds 10 --out /tmp/x.mp4   # no API
```
Needs `TYPESAFE_API_KEY` in env (read by typesafe-sdk; never printed).

## Swap ROM / game (config change)
`config.json`: `game` (`thwaite` | `smb`), `rom` (path), `title`, `seconds`, `out`, `frames_per_decision`.
**Super Mario Bros.:** put a legally obtained ROM (dump of your own cartridge; iNES, mapper 0, ~40 KB) at
`/workspace/jev-plays/roms/super-mario-bros.nes`, then:
```bash
../.venv/bin/python -m jevplay.run --game smb --rom /workspace/jev-plays/roms/super-mario-bros.nes \
  --title "Jev plays Mario" --seconds 60 --out /workspace/reports/jev-plays/jev-plays-mario-1-1.mp4
```
The run stops at the flag (level clear) or game over and holds the last frame 1 s.
New game = add an adapter class in `games.py` (options, buttons, read_state, situation, raw_lines, reward).

## Swap model
`policies.py`: any class with `choose(state, options, instructions, fallback, situation) -> Decision`.
`policy: "rule"` = offline stand-in.

---
## Update 2026-10-17: Mario 1-1 cleared (final pipeline)

**Final:** `jev-plays-mario-1-1.mp4` (1920x1080 H.264 + AAC game audio, 25.6 s) and `.jsonl` (every decision).
Run: `cd /workspace/jev-plays/pipeline && ../.venv-retro/bin/python -m jevplay.run --model jev-preview --seconds 40 --out OUT.mp4`
(config.json: game=smb, rom=/workspace/jev-plays/roms/super-mario-bros.nes, title "Jev plays Mario").

- Emulator: stable-retro FCEUmm core (`jevplay/retro_env.py`, nes-py-compatible): video, RAM, multi-slot
  save-states and APU audio from ONE emulator. Audio captured per emulated frame (previews discarded), so it
  stays in sync while the game is paused for decisions. (nes-py has no audio; replaying inputs in a second core
  drifted after ~14 s, so the backend was switched.) `--emulator nes-py` still works without audio.
- Jev input per decision (one request, `jev-preview` = jev-1.14.0): `state = {"frame": <PNG of the screen,
  upscaled to 512 px wide>, "ram_facts": {...}}` + header `TypeSafe-Danger-Zone-Images: true`.
  ram_facts = exact memory values (Mario x/y/speed/on_ground, enemies by type/dx/dy, pits/obstacles from the tile
  buffer) and **exact emulator previews** (save-state rollouts: does each jump land safely, frames until death if
  never jumping, run-now-jump-next). Physics stays in code; Jev chooses.
- Questions: Choice over 7 actions with each option's preview inside its structured criteria (bars + confidence),
  Noul "must jump now", Noul "immediate danger" (bar coral > 0.9).
- Jump actions are code-executed macros (A held to the top of the arc; mid-air input stops on touchdown).
- ROM: from the public PyPI wheel gym-super-mario-bros 9.1.0 (approved for private recording). `roms/` and `*.nes`
  are gitignored; never committed.
- `jevplay/clip.py` = zero-API replay renderer (nes-py runs); `jevplay/replay.py` = state reconstruction.

### Image input (docs read: llms.txt, images.md, api.md, models.md, primitives/noul.md, sdk/python changelog
+ types/assets, typesafe-SKILL.md)
- Dreamer/prototype status; needs header `TypeSafe-Danger-Zone-Images: true`; Jev 1.13 and 1.14 accept images.
- PNG or JPEG stills; 16 MiB/file; each side 280-1568 px (NES 256x240 must be upscaled); 78,400-2,458,624 px total;
  32 MiB request. Put an image used by several questions in `state` once (each reference adds it again).
- Billed as input tokens at normal price ($0.042 / M input tokens, output free). Measured: a 512x480 frame adds
  ~760 tokens (~1.9k text+image vs ~1.2k text only); full Mario request with previews ~2.4k tokens; latency
  median ~80 ms, p90 ~120-150 ms.
