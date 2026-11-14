"""Builds' head: your own record, from your own builds' ledgers.

``of(rows)`` reads the Builds list's rows (``api/state``' ``builds``): every build the loop
started that has finished a round (not queued, not running, with a cost in its ledger) counts.
It says how many, the median time and the median cost of a build, what they cost in all, and
how many were published and how many wait held. A build by the lean flow is counted beside
the others and also on its own, so the unproven flow's numbers are never mixed in unseen.
Nothing here is an estimate: a build with no cost recorded is left out, and said."""

from __future__ import annotations

from statistics import median

RUNNING = ("running", "queued", "briefing", "brief")


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _part(rows: list[dict]) -> dict:
    costs = [c for c in (_num(r.get("cost_usd")) for r in rows) if c is not None]
    walls = [w for w in (_num(r.get("wall_s")) for r in rows) if w is not None]
    return {
        "builds": len(rows),
        "median_usd": round(median(costs), 2) if costs else None,
        "median_s": round(median(walls)) if walls else None,
        "total_usd": round(sum(costs), 2) if costs else None,
    }


def of(rows: list[dict]) -> dict:
    """``{"builds", "median_usd", "median_s", "total_usd", "published", "held",
    "no_cost", "lean": {...} or None}``."""
    mine = [
        r
        for r in rows or []
        if isinstance(r, dict)
        and r.get("idea")
        and r.get("kind") != "fix"
        and str(r.get("status") or "") not in RUNNING
        and not r.get("live")
    ]
    counted = [r for r in mine if _num(r.get("cost_usd")) is not None]
    out = _part(counted)
    out["published"] = sum(1 for r in counted if r.get("url"))
    out["held"] = sum(1 for r in counted if r.get("idea_state") == "held")
    out["no_cost"] = len(mine) - len(counted)
    lean = [r for r in counted if r.get("flow") == "lean"]
    out["lean"] = _part(lean) if lean else None
    return out
