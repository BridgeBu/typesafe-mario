"""Decision interface: (state dict, {option: description}, instructions) -> Decision.
Swap models by adding a class with the same choose() signature."""
from __future__ import annotations
import time
from dataclasses import dataclass, field


@dataclass
class Decision:
    action: str
    probabilities: dict[str, float]
    confidence: float
    latency_ms: float
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    error: str = ""
    must_act: float | None = None
    danger: float | None = None


class JevPolicy:
    """TypeSafe Jev via typesafe-sdk, same call shape as typesafe-mario's TypeSafePolicy.
    Reads TYPESAFE_API_KEY from env (TypeSafeClient default)."""

    def __init__(self) -> None:
        from typesafe_sdk import Choice, Noul, NoulCriteria, TypeSafeClient
        self._Choice, self._Noul, self._NC = Choice, Noul, NoulCriteria
        self._client = TypeSafeClient()

    def choose(self, state, options, instructions, fallback, situation=None):
        t = time.perf_counter()
        try:
            q = {"next_action": self._Choice(instructions=instructions, criteria=options)}
            if situation:
                for k in ("must_act", "danger"):
                    instr, (yes, no) = situation[k]
                    q[k] = self._Noul(instructions=instr, criteria=self._NC(true=yes, false=no))
            r = self._client.system_one(model="jev-latest", state=state, questions=q)
            ms = (time.perf_counter() - t) * 1000
            a = r.answers["next_action"]
            u = getattr(r, "usage", None)
            d = Decision(str(a.choice), {k: float(v) for k, v in dict(a.probabilities).items()},
                         float(a.confidence), ms, getattr(r, "model", "jev"),
                         getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)
            if situation:
                d.must_act = float(r.answers["must_act"].noul)
                d.danger = float(r.answers["danger"].noul)
            return d
        except Exception as exc:  # keep recording; fall back to rule AI for this step
            d = fallback.choose(state, options, instructions, None, situation)
            d.latency_ms = (time.perf_counter() - t) * 1000
            d.error = type(exc).__name__
            return d

    def close(self):
        c = getattr(self._client, "close", None)
        if callable(c):
            c()


class RulePolicy:
    """Stand-in when no model key: delegates to the game adapter's heuristic."""

    def __init__(self, heuristic):
        self.heuristic = heuristic

    def choose(self, state, options, instructions, fallback, situation=None):
        scores = self.heuristic(state, list(options))
        tot = sum(scores.values()) or 1.0
        probs = {k: v / tot for k, v in scores.items()}
        best = max(probs, key=probs.get)
        return Decision(best, probs, probs[best], 0.0, "rule-ai")

    def close(self):
        pass
