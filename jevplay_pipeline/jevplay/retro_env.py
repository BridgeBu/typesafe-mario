"""nes-py-compatible wrapper around stable-retro's FCEUmm core: same video, RAM, save-states AND APU audio."""
import numpy as np
import stable_retro as r

ORDER = [2, 0, 4, 8, 16, 32, 64, 128, 1]  # retro NES mask order: B,(Y),SELECT,START,UP,DOWN,LEFT,RIGHT,A


class _Ram:
    def __init__(self, env): self.env = env
    def _buf(self):
        e = self.env
        if e._ram_cache is None:
            b = e._data.memory.blocks
            e._ram_cache = np.frombuffer(bytes(b[0]) + bytes(b[1024]), np.uint8)
        return e._ram_cache
    def __getitem__(self, k): return self._buf()[k]
    def __len__(self): return 2048


class RetroNES:
    def __init__(self, rom):
        self.em = r.RetroEmulator(rom)
        self._data = r.data.GameData(); self.em.configure_data(self._data)
        self._ram_cache = None; self.ram = _Ram(self)
        self._stack, self.audio = [], []
        self.audio_rate = self.em.get_audio_rate()

    def reset(self):
        return self.em.get_screen()

    def step(self, btn):
        self.em.set_button_mask(np.array([1 if (k and btn & k) else 0 for k in ORDER], np.uint8), 0)
        self.em.step(); self._ram_cache = None
        self.audio.append(self.em.get_audio().copy())
        return self.em.get_screen(), 0.0, False, False, {}

    def _backup(self):
        self._stack.append((self.em.get_state(), len(self.audio)))

    def _restore(self):
        st, n = self._stack.pop()
        self.em.set_state(st); self._ram_cache = None
        del self.audio[n:]  # preview audio is discarded

    def close(self):
        pass
