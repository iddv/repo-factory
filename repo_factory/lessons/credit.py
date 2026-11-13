"""Credit assignment (build v5 §5.4): per v5 run, from ``claims.json``, ``findings.json`` and
the panel's classes. Needs no hidden suite, so it also runs in production.

| event                                                         | counter                       |
|---------------------------------------------------------------|-------------------------------|
| the lesson was injected                                       | ``exposures += 1``            |
| a claim cites it (``lessons: [L007]``)                        | ``derived += 1`` per claim    |
| a scenario from such a claim fails and the panel confirms it  | ``caught += 1`` (once per run)|
|                                                               | and the pack joins            |
|                                                               | ``packs_caught_on``           |
| such a scenario fails and the panel votes ``not_required``    | ``false_alarms += 1`` per     |
|                                                               | scenario                      |

``caught`` counts runs, so ``hit_rate = caught / exposures`` reads "found a defect in 3 of 6
builds" (§5.1). Counters decide retirement and ordering, never promotion:

- ``probation -> active``: ``caught >= 2`` on at least two different packs;
- retire: ``exposures >= 4`` and ``caught == 0``; or ``false_alarms >= 3`` and
  ``false_alarms > caught``.

A scenario's lessons are its own ``lessons`` field, else its claim's. The panel's class is the
scenario's ``class`` (Track D writes ``defect | retired | interpretation`` into
``findings.json``), else ``oracle.json`` ``{scenario id: class}`` beside it."""

from __future__ import annotations

from . import store

PROMOTE_CAUGHT, PROMOTE_PACKS = 2, 2
RETIRE_EXPOSURES = 4
RETIRE_FALSE_ALARMS = 3


def injected_ids(
    *, claims: dict | None, run: dict | None, data: dict, pack: str | None
) -> list[str]:
    """The lessons the run was shown: ``lessons_injected`` from ``claims.json`` or
    ``run.json`` when the pipeline records it, else what ``for_build`` gives for this pack
    from the file being credited (the same file the run used, if nothing changed since)."""
    for src in (claims or {}, run or {}):
        ids = src.get("lessons_injected")
        if isinstance(ids, list):
            return [str(x) for x in ids]
    return [x["id"] for x in store.live(data) if pack is None or x["born"].get("pack") != pack]


def _claims_by_id(claims: dict | None) -> dict[str, dict]:
    return {str(c.get("id")): c for c in (claims or {}).get("claims") or [] if isinstance(c, dict)}


def _cited(c: dict | None) -> list[str]:
    return [str(x) for x in (c or {}).get("lessons") or []]


def credit(
    data: dict,
    *,
    pack: str,
    claims: dict | None,
    findings: dict | None,
    injected: list[str],
    run_ref: str | None = None,
    oracle: dict | None = None,
) -> dict:
    """Update the counters in ``data`` (a candidate lessons file) for one run. A run already
    credited (``credited`` in the file) changes nothing. Returns what changed per lesson."""
    done = data.setdefault("credited", [])
    if run_ref and run_ref in done:
        return {"skipped": f"{run_ref} was already credited"}
    known = {x["id"]: x for x in data["lessons"]}
    delta: dict[str, dict] = {}

    def bump(lid, key, n=1):
        if lid in known:
            known[lid]["stats"][key] += n
            delta.setdefault(lid, {}).setdefault(key, 0)
            delta[lid][key] += n

    for lid in dict.fromkeys(injected):
        bump(lid, "exposures")
    by_claim = _claims_by_id(claims)
    for c in by_claim.values():
        for lid in dict.fromkeys(_cited(c)):
            bump(lid, "derived")
    caught: set[str] = set()
    for s in (findings or {}).get("scenarios") or []:
        if s.get("status") != "fail":
            continue
        lids = _cited(s) or _cited(by_claim.get(str(s.get("claim"))))
        cls = s.get("class") or (oracle or {}).get(str(s.get("id")))
        for lid in dict.fromkeys(lids):
            if cls == "defect":
                caught.add(lid)
            elif cls == "retired":
                bump(lid, "false_alarms")
    for lid in caught:
        bump(lid, "caught")
        if lid in known and pack not in known[lid]["stats"]["packs_caught_on"]:
            known[lid]["stats"]["packs_caught_on"].append(pack)
    for x in data["lessons"]:
        x["hit_rate"] = store.hit_rate(x)
    if run_ref:
        done.append(run_ref)
    return {"lessons": delta, "transitions": apply_rules(data)}


def apply_rules(data: dict, today: str | None = None) -> list[dict]:
    """Status changes from the counters (§5.4). Returns ``{id, from, to, why}`` per change."""
    out = []
    for x in data["lessons"]:
        s, before = x["stats"], x["status"]
        if before == "retired":
            continue
        if s["exposures"] >= RETIRE_EXPOSURES and s["caught"] == 0:
            store.retire(x, f"exposed {s['exposures']} times, caught nothing", today)
        elif s["false_alarms"] >= RETIRE_FALSE_ALARMS and s["false_alarms"] > s["caught"]:
            store.retire(
                x, f"{s['false_alarms']} false alarms against {s['caught']} catches", today
            )
        elif (
            before == "probation"
            and s["caught"] >= PROMOTE_CAUGHT
            and len(set(s["packs_caught_on"])) >= PROMOTE_PACKS
        ):
            x["status"] = "active"
        if x["status"] != before:
            out.append(
                {
                    "id": x["id"],
                    "from": before,
                    "to": x["status"],
                    "why": (x.get("retired") or {}).get("why")
                    or f"caught {s['caught']} on {len(set(s['packs_caught_on']))} packs",
                }
            )
    return out
