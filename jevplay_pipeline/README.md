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
