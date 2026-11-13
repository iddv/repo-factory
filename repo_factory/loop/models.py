"""The loop's model calls: ``model(prompt) -> (text, usd)``.

The real one is ``claude -p`` without tools (``costs.ledger.call_claude``); tests pass any
callable with the same shape. The real one's reply is a ``Costed`` 2-tuple that also carries
the call's ``tokens`` (``{"in", "out"}``), so ``text, usd = model(prompt)`` keeps working;
``split`` reads ``(value, usd, tokens)`` from any reply: a ``Costed``, a plain 2-tuple (no
tokens: None) or a 3-tuple ``(text, usd, tokens)``."""

from __future__ import annotations

from collections.abc import Callable

Model = Callable[[str], "tuple[str, float]"]


class Costed(tuple):
    """``(value, usd)`` with the call's tokens on ``.tokens`` (None when not known)."""

    def __new__(cls, value, usd, tokens: dict | None = None):
        obj = super().__new__(cls, (value, usd))
        obj.tokens = tokens
        return obj


def split(reply) -> tuple:
    """``(value, usd, tokens)`` from a model reply (see the module's doc)."""
    if isinstance(reply, tuple) and len(reply) >= 3:
        return reply[0], reply[1], _pair(reply[2])
    value, usd = reply
    return value, usd, _pair(getattr(reply, "tokens", None))


def _pair(t) -> dict | None:
    if isinstance(t, dict) and (t.get("in") is not None or t.get("out") is not None):
        return {"in": int(t.get("in") or 0), "out": int(t.get("out") or 0)}
    if isinstance(t, (tuple, list)) and len(t) == 2:
        return {"in": int(t[0] or 0), "out": int(t[1] or 0)}
    return None


def add(a: dict | None, b: dict | None) -> dict | None:
    """The sum of two token pairs; None only when both are None."""
    if b is None:
        return a
    if a is None:
        return dict(b)
    return {"in": a["in"] + b["in"], "out": a["out"] + b["out"]}


def spend_kw(tokens: dict | None) -> dict:
    """``Ledger.spend``'s token keywords for a pair (nothing for None)."""
    return {"in_tokens": tokens["in"], "out_tokens": tokens["out"]} if tokens else {}


def claude(model: str | None = None, purpose: str = "pitch", timeout_s: int = 600) -> Model:
    def call(prompt: str) -> Costed:
        from ..costs.ledger import call_claude, usage_of

        text, data = call_claude(prompt, model, timeout_s, caller="loop", purpose=purpose)
        use = usage_of(data) if data.get("usage") else {}
        tokens = {"in": use["in_tokens"], "out": use["out_tokens"]} if use else None
        return Costed(text, float(data.get("total_cost_usd") or 0.0), tokens)

    return call
