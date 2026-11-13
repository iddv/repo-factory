"""The closing loop: the factory's own review ("2-opt") pass after the last milestone.

    mine (interactions + edges) -> score -> arbitrate -> fix -> repeat

until a round finds no new failing active rules that the arbiter blames on the code, or the
round cap is hit (``policies/v2/rules.yaml`` ``ruleset.review_rounds``, default 3).

The loop is written against callables so it runs the same with the live executor (a fix
is a resumed worker session on the lineage's head with the failing rules as its prompt)
and in unit tests:

- ``mine(round, head) -> dict``: mine and validate new rules against ``head``'s code
  (``{"active": n, ...}``);
- ``score(head) -> dict``: rule id -> outcome over every due active rule (visible and
  held);
- ``arbitrate(head, failing_ids, results) -> dict``: at least ``{"code_wrong": [ids]}``;
- ``fix(head, rule_ids, round) -> head | None``: a new head that was accepted (see
  ``accept``), or None when the fix was rejected or not possible;
- ``visible(rule_id) -> bool``: whether a worker may see the rule (held rules are never
  sent to a fix; they keep measuring);
- ``emit(type, **fields)``: the ``review_round`` event."""

from __future__ import annotations

from .score import regressions

DEFAULT_ROUNDS = 3


def accept(before: dict, after: dict) -> bool:
    """A fix is kept when it passes more rules than before and breaks none that passed."""

    def n(res):
        return sum(1 for v in res.values() if v.get("status") == "passed")

    return n(after) > n(before) and not regressions(before, after)


def review_loop(
    head,
    *,
    mine,
    score,
    arbitrate,
    fix,
    visible=lambda rid: True,
    emit=lambda *a, **k: None,
    cap: int = DEFAULT_ROUNDS,
    prefetch=None,
) -> dict:
    """Returns {"head", "rounds": [...], "stop": "clean"|"no_new_failures"|"cap"|"no_fix"}.

    ``prefetch(k, head) -> get``: start round ``k``'s mining in the background against
    ``head`` and return a function that waits for it (and admits its rules). With it, the
    next round's mining runs while this round's fix does (``mining.async``); the next
    round then scores the fixed head on the rules mined against the code before the fix."""
    rounds, sent, stop = [], set(), "cap"
    pending = None
    for k in range(1, max(0, cap) + 1):
        mined = (pending() if pending is not None else mine(k, head)) or {}
        pending = None
        results = score(head)
        failing = sorted(i for i, v in results.items() if v.get("status") != "passed")
        arb = arbitrate(head, failing, results) or {}
        blamed = sorted(set(arb.get("code_wrong") or []) & set(failing))
        to_fix = [i for i in blamed if visible(i)]
        new = [i for i in to_fix if i not in sent]
        rec = {
            "round": k,
            "head": head,
            "mined": mined.get("active", 0),
            "rules": len(results),
            "passed": len(results) - len(failing),
            "failing": len(failing),
            "code_wrong": len(blamed),
            "held_code_wrong": len(blamed) - len(to_fix),
            "new": len(new),
            "rule_wrong": len(arb.get("retired") or []) + len(arb.get("repaired") or []),
            "ambiguous": len(arb.get("disputed") or []),
        }
        if not new:
            stop = "clean" if not blamed else "no_new_failures"
            rec.update(fixed=None, stop=stop)
            rounds.append(rec)
            emit("review_round", **rec)
            break
        sent |= set(new)
        if prefetch is not None and k < cap:
            pending = prefetch(k + 1, head)
        new_head = fix(head, to_fix, k)
        rec["fixed"] = new_head is not None
        if new_head is not None:
            rec["new_head"] = new_head
            head = new_head
        rounds.append(rec)
        emit("review_round", **rec)
    else:
        stop = "cap" if cap > 0 else "disabled"
    return {"head": head, "rounds": rounds, "stop": stop}
