"""Acceptance after a repair (docs/design/build-v5.md §3.5): the authority order, strict
improvement and regressions, as pure functions.

A *measure* is the harness's record of one head::

    {"valid": bool,                      # the user's own tests pass (rank 1)
     "scenarios": {id: status},          # visible verifier scenarios
     "held": {id: status},               # held scenarios (never in the builder's workspace)
     "tests": {test id: status},         # the builder's own tests
     "user": {test id: status}}          # the user's tests (task.yaml / the seed repo)

``decide`` counts the confirmed defects fixed and lists every regression; a regression is
never an automatic rejection: each goes through the conflict protocol (``oracle.pair``, then
``resolve`` here, which applies the authority order). ``n_passed``, ``test_regressions`` and
``objective`` are build v4's (``core/oneshot.py``, frozen), copied with their tests."""

from __future__ import annotations

AUTHORITY = {
    1: "the owner's answers and rulings; the user's own tests; the owner's safe-defaults "
    "rule (readiness)",
    2: "a claim with basis spec_explicit and 3 of 3 required; a must-have flow of SCOPE.md "
    "that doesn't work end to end; a security finding",
    3: "a claim with at least 2 of 3 required; any other incomplete finding; a readiness gap",
    4: "the builder's own tests",
    5: "interpretation defaults the factory chose itself; anything traced to its own assumptions",
}
ASSUMPTION_REFS = ("answer:assumptions", "assumption", "default")


# -- the authority order -----------------------------------------------------------------------
def rank(item: dict) -> int:
    """An item's rank (1 is the highest authority). ``item["kind"]``: ``owner`` or
    ``user_test`` (1); ``scenario`` (a verifier scenario: 2 or 3 by its panel votes, 5 if it
    traces to the factory's own assumptions or was never confirmed); ``builder_test`` (4, or
    5 if it traces to the factory's assumptions); ``interpretation`` (5, or 1 when the owner
    chose it)."""
    kind = item.get("kind")
    ref = str(item.get("source_ref") or "")
    own = any(ref.startswith(a) for a in ASSUMPTION_REFS) or item.get("basis") == "default"
    if kind in ("owner", "user_test"):
        return 1
    if kind == "interpretation":
        return 1 if item.get("chosen_by") == "owner" else 5
    if kind == "builder_test":
        return 5 if own else 4
    if kind == "scenario":
        if item.get("basis") in ("owner", "owner_rule"):
            return 1
        # The readiness stage (Addendum C): a security finding is rank 2, a readiness gap
        # rank 3; the safe defaults (localhost, empty start, no default password) are the
        # owner's standing rule ("owner_rule", rank 1).
        # A service that can't start again at once after a stop or a crash (it loses
        # requests) is a reliability bug: rank 2 too.
        if item.get("basis") in ("security", "reliability"):
            return 2
        if item.get("basis") == "readiness":
            return 3
        # An incomplete finding (a flow of SCOPE.md that doesn't work end to end, a stub, a
        # dead control): a must-have flow is rank 2, anything else incomplete rank 3.
        if item.get("basis") == "scope_must":
            return 2
        if item.get("basis") == "incomplete":
            return 3
        if own:
            return 5
        req, n = int(item.get("required") or 0), int(item.get("votes") or 3)
        if item.get("basis") == "spec_explicit" and n and req == n and n >= 3:
            return 2
        if req >= 2:
            return 3
        return 5
    return 5


def resolve(verdict: str, x_rank: int, y_rank: int) -> str:
    """The action for a pair (X fixed, Y broken): ``fixup`` (compatible: one fix-up with
    both; if Y still fails, X's commit is reverted), ``keep_x`` (Y is wrong or stale),
    ``revert_x``, or ``interpretation`` (undecidable at equal rank). The panel is asked
    about every pair, but for incompatible pairs of different rank the higher rank wins and
    the panel's ``x_wins``/``y_wins`` is ignored."""
    if verdict == "compatible":
        return "fixup"
    if x_rank != y_rank:
        return "keep_x" if x_rank < y_rank else "revert_x"
    return {"x_wins": "keep_x", "y_wins": "revert_x"}.get(verdict, "interpretation")


# -- strict improvement (build v4, copied) --------------------------------------------------
def n_passed(statuses: dict) -> int:
    return sum(1 for v in (statuses or {}).values() if v == "passed")


def test_regressions(before: dict, after: dict) -> list[str]:
    """Tests that passed before and fail now: visible (user, own, acceptance) and held-back.
    A canonical test (user, acceptance, held-back) that disappeared counts too; an own test
    the repair removed does not."""
    canon = set(before.get("canonical") or [])
    out = []
    for group in ("tests", "held"):
        for t, st in (before.get(group) or {}).items():
            if st != "passed":
                continue
            now = (after.get(group) or {}).get(t)
            if now == "passed":
                continue
            if (
                now in ("failed", "error")
                or t.startswith(("acceptance/", "_factory_held/"))
                or t.split("::")[0] in canon
            ):
                out.append(t)
    return sorted(out)


def objective(before: dict, after: dict) -> tuple[bool, str]:
    """Build v4's objective half of the strict-improvement rule: the user tests pass; rules
    passed and held-back tests passed are each >= before and their total is > before; no rule
    or test that passed before fails. Returns (ok, why)."""
    if not after.get("valid"):
        return False, "rejected: the user tests fail after the repair"
    rb, ra = n_passed(before.get("rules")), n_passed(after.get("rules"))
    hb, ha = n_passed(before.get("held")), n_passed(after.get("held"))
    broke = sorted(
        r
        for r, st in (before.get("rules") or {}).items()
        if st == "passed" and (after.get("rules") or {}).get(r) != "passed"
    )
    tests = test_regressions(before, after)
    if broke or tests:
        what = [f"{len(broke)} rule(s)" if broke else "", f"{len(tests)} test(s)" if tests else ""]
        return False, (
            "rejected: it broke "
            + " and ".join(w for w in what if w)
            + " that passed before: "
            + ", ".join((broke + tests)[:6])
        )
    if ra < rb:
        return False, f"rejected: rules passed fell {rb} -> {ra}"
    if ha < hb:
        return False, f"rejected: held-back tests passed fell {hb} -> {ha}"
    if ra + ha <= rb + hb:
        return False, f"rejected: no improvement (rules {rb} -> {ra}, held-back {hb} -> {ha})"
    return True, f"accepted: rules {rb} -> {ra}, held-back tests {hb} -> {ha}, nothing broken"


# -- v5 acceptance ----------------------------------------------------------------------------
GROUPS = ("scenarios", "held", "tests", "user")


def regressions(before: dict, after: dict) -> list[dict]:
    """Everything that passed before and doesn't now: ``[{"id", "group", "now"}]``. A
    scenario or user test that disappeared counts; a builder's test the repair removed does
    not (as build v4)."""
    out = []
    for g in GROUPS:
        for t, st in (before.get(g) or {}).items():
            if st != "passed":
                continue
            now = (after.get(g) or {}).get(t)
            if now == "passed" or (g == "tests" and now is None):
                continue
            out.append({"id": t, "group": g, "now": now or "missing"})
    return out


def fixed(before: dict, after: dict, sent) -> list[str]:
    """The sent findings (scenario ids) that failed before and pass now."""
    out = []
    for i in sent:
        b = (before.get("scenarios") or {}).get(i) or (before.get("held") or {}).get(i)
        a = (after.get("scenarios") or {}).get(i) or (after.get("held") or {}).get(i)
        if b != "passed" and a == "passed":
            out.append(i)
    return sorted(out)


def decide(before: dict, after: dict, sent) -> dict:
    """``{"valid", "fixed", "regressions", "action", "why"}``; ``action``: ``accept`` (no
    regressions, >= 1 fix), ``no_fix`` (nothing broken, nothing fixed), ``conflicts`` (each
    regression goes through the conflict protocol; a user test that broke is one, of rank
    1), ``reject`` (the head is not valid though no single test explains it, e.g. the
    user's suite no longer runs)."""
    fx = fixed(before, after, sent)
    regs = regressions(before, after)
    # The user's tests are rank 1: the head must pass them. When they already failed before
    # the repair, the repair isn't held to a bar the base build didn't meet.
    valid = bool(after.get("valid")) or not before.get("valid")
    if regs:
        action, why = (
            "conflicts",
            (
                f"{len(fx)} fixed, {len(regs)} regression(s): "
                + ", ".join(r["id"] for r in regs[:6])
            ),
        )
    elif not valid:
        action, why = "reject", "the user's tests fail after the repair"
    elif fx:
        action, why = "accept", f"{len(fx)} fixed, nothing broken"
    else:
        action, why = "no_fix", "nothing fixed, nothing broken"
    return {"valid": valid, "fixed": fx, "regressions": regs, "action": action, "why": why}


def status_of(measure: dict, group: str, item: str) -> str | None:
    return (measure.get(group) or {}).get(item)


def blame(prefixes: list[dict], reg: dict) -> int:
    """The index of the first commit after which ``reg`` fails, given the measure after
    each commit in order (``prefixes[i]``: the head at commit i). A regression that only
    shows at the head (or in none of the prefixes: flaky) is blamed on the last commit."""
    for i, m in enumerate(prefixes):
        st = status_of(m, reg["group"], reg["id"])
        if st != "passed" and not (reg["group"] == "tests" and st is None):
            return i
    return max(0, len(prefixes) - 1)
