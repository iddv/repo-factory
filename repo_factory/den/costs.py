"""Per-run worker cost, including dens recorded before resumed sessions were costed right.

A resumed (``--resume --fork-session``) Claude Code run reports ``total_cost_usd`` and
``modelUsage`` cumulatively over the whole session history. Since the fix, the harness
stores the run's own cost in ``nodes.cost_usd`` and the cumulative figure in the node's
``action_json.session_cost_usd``. Older nodes stored the cumulative figure in ``cost_usd``;
``run_costs`` recovers their own cost as cumulative minus the parent session's cumulative
cost (the node whose ``session_id`` the run ``resumed``)."""

from __future__ import annotations

import json


def _action(n: dict) -> dict:
    a = n.get("action")
    if a is None:
        try:
            a = json.loads(n.get("action_json") or "{}")
        except (TypeError, ValueError):
            a = {}
    return a if isinstance(a, dict) else {}


def run_costs(nodes: list[dict]) -> dict[int, float | None]:
    """Node id -> this run's own cost in USD (None where unknown). ``nodes``: den node rows
    (``id``, ``cost_usd`` and ``action`` or ``action_json``)."""
    acts = {n["id"]: _action(n) for n in nodes}
    by_session = {a["session_id"]: n for n in nodes if (a := acts[n["id"]]).get("session_id")}
    out: dict[int, float | None] = {}
    for n in nodes:
        a, c = acts[n["id"]], n.get("cost_usd")
        if c is None or "session_cost_usd" in a or not a.get("resumed"):
            out[n["id"]] = c  # already per run (or never resumed)
            continue
        p = by_session.get(a["resumed"])
        base = None if p is None else acts[p["id"]].get("session_cost_usd", p.get("cost_usd"))
        out[n["id"]] = round(max(0.0, float(c) - float(base)), 7) if base is not None else c
    return out
