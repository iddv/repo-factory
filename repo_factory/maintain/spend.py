"""Maintenance's own monthly line and limit (Settings › Spend).

Every dollar maintenance spends is in the loop's spend table under one of ``KINDS`` (a fix's
sessions are ``revise``; a project's proposals ``evolve``), so it counts against the daily cap
as everything does, and here against ``maintain.monthly_usd`` (default $20, null: no limit;
0 or less is a wrong value that counts as reached, never as no limit). A fix's own cap is
``maintain.max_usd`` (default $5); every session of a fix runs under the smaller of what is
left of both (``revise.Fix.cap``).

At the limit the poll starts no fix and no proposal run, and the page says so in words
(``words``). A fix already running stops before its next session."""

from __future__ import annotations

import time

from ..loop.ledger import MAINTAIN_KINDS, PROJECT_KINDS, day_start

KINDS = tuple(dict.fromkeys((*MAINTAIN_KINDS, *PROJECT_KINDS)))
DEFAULT_MONTHLY = 20.0


def month_start(now: float | None = None) -> float:
    t = time.localtime(now or time.time())
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def spent(ledger, now: float | None = None, since: float | None = None) -> float:
    start = month_start(now) if since is None else since
    return round(sum(ledger.own_spend(start, None, k) for k in KINDS), 4)


def limit(cfg) -> float | None:
    """``maintain.monthly_usd``: a positive number, or None (no limit). A wrong value (0,
    below 0, text) is a limit of nothing: maintenance spends nothing until it is fixed."""
    m = cfg.get("maintain") or {}
    v = m.get("monthly_usd", DEFAULT_MONTHLY) if isinstance(m, dict) else DEFAULT_MONTHLY
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0
    return float(v) if v > 0 else 0.0


def left(loop, now: float | None = None) -> float | None:
    """Dollars maintenance may still spend this month (and today, under ``daily_usd``); None:
    no limit applies."""
    out = []
    lim = limit(loop.cfg)
    if lim is not None:
        out.append(lim - spent(loop.ledger, now))
    day = loop.cfg.get("daily_usd")
    if isinstance(day, (int, float)) and not isinstance(day, bool):
        out.append(float(day) - float(loop.spend()["today"]["total"]) if day > 0 else 0.0)
    return round(max(0.0, min(out)), 4) if out else None


def reached(loop, now: float | None = None, floor: float = 0.05) -> str | None:
    """Why maintenance may spend nothing now, in words, or None."""
    lim = limit(loop.cfg)
    if lim is not None:
        s = spent(loop.ledger, now)
        if lim <= 0:
            return (
                "maintenance spends nothing: maintain.monthly_usd is not a positive amount "
                "(set it in Settings › Spend)"
            )
        if s >= lim - floor:
            return (
                f"maintenance paused: this month's maintenance spend ${s:.2f} reached its limit "
                f"${lim:.2f} (Settings › Spend); it goes on next month, or when you raise it"
            )
    lt = left(loop, now)
    if lt is not None and lt < floor:
        return "maintenance paused: today's spend reached the daily cap (Settings › Spend)"
    return None


def summary(loop, now: float | None = None) -> dict:
    """Settings › Spend's maintenance line."""
    now = now or time.time()
    return {
        "month": spent(loop.ledger, now),
        "today": spent(loop.ledger, now, since=day_start(now)),
        "limit": limit(loop.cfg),
        "per_fix": (loop.cfg.get("maintain") or {}).get("max_usd"),
        "paused": reached(loop, now),
        "since": month_start(now),
    }
