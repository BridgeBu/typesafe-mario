"""Zero-API replay: re-run logged actions deterministically and return env at a given decision index."""
import json, sys
from nes_py import NESEnv
from . import games  # noqa
from .smb import SMB


def replay(jsonl, upto, rom="/workspace/jev-plays/roms/super-mario-bros.nes", boot_delay=0):
    L = [json.loads(l) for l in open(jsonl)]
    g = SMB(); g.boot_delay = boot_delay
    e = NESEnv(rom); e.reset(); frame = g.boot(e)
    for d in L[:upto]:
        g.read_state(e, frame)
        for b in g.macro(e, d["action"]):
            frame, *_ = e.step(b)
    return g, e, frame, L
