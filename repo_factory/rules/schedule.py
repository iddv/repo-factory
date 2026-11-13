"""When the rule set mines (build v3 §2): the ``mining:`` settings and the mine points.

Settings come from the policy's ``rules.yaml`` under ``mining:`` (``DEFAULTS``). The
defaults reproduce v2: mine after every milestone, synchronously, on the critical path.

- ``schedule: every_milestone`` (v2): ``ruleset.first`` after milestone 1,
  ``ruleset.after_milestone`` after every later planned milestone.
- ``schedule: planned``: the planner's mine points (``points``):
  1. ``plan``: before building, ``plan_modes`` (requirements + interactions) mined from
     the spec, the plan and the interface, with no code. Rules pass (a) and (b) then and
     wait as candidates; (c) and (d) run on milestone 1's kept code (the ``validate``
     point after m1, which mines nothing);
  2. ``interactions`` after each milestone the planner flagged as touching earlier ones
     (``interacts_with``; a planner that gives no flags at all falls back to
     ``depends_on``);
  3. one ``end_modes`` (edges) pass after the last planned milestone, which with
     ``async`` runs alongside the final integration pass.

- ``async``: a pass runs in a background thread while the next worker builds; its rules
  join the next gate and its failures become fix items (``core.mining``).
- ``review_rounds``: the closing loop's cap (``null``: ``ruleset.review_rounds``).
- ``promote_held``: a held rule the arbiter says caught a real bug becomes visible for
  the fix loop, and a held replacement is mined in the same area.

Mining that scales with the task (build v3 §7c; all off by default, as in v2):

- ``coalesce``: at most one pass waits behind the running one. A new request replaces the
  waiting one (modes merged), and the pass mines against the latest kept head when it
  starts (``core.mining``).
- ``rules_per_milestone`` / ``max_rules``: the build's rule budget (``budget``):
  ``rules_per_milestone`` x planned milestones, capped at ``max_rules``. Each pass asks the
  miner for its share of what is left (``allot``), split over its modes. Once the budget
  is spent, a pass runs only when a gate is failing in an area
  (``MiningController.gate_failing``).
- ``small_plan_milestones`` / ``small_plan_sizes``: a plan with at most that many
  milestones, or whose milestones all have one of those sizes, gets only the plan pass
  and one closing pass (``small``).
- ``close_early``: the closing pass (the last mine point) starts when the last planned
  milestone's worker launches, in the background against the head before it. Review
  round 1 then scores on that pass's rules instead of mining again.
- ``closing_rules``: the closing pass's cap (the end of the tour may wait for that pass);
  the rest of the budget goes to the earlier passes.
- ``combine``: a pass with several code modes (interactions + edges) is one miner call.
- ``lean_prompt``: the miner gets the existing rules as titles only and a code digest
  scoped to source files (no tests, no docs)."""

from __future__ import annotations

SCHEDULES = ("every_milestone", "planned")

DEFAULTS = {
    "schedule": "every_milestone",
    "async": False,
    "review_rounds": None,
    "promote_held": False,
    "plan_modes": ["requirements", "interactions"],
    "end_modes": ["edges"],
    "coalesce": False,
    "rules_per_milestone": None,
    "max_rules": None,
    "small_plan_milestones": None,
    "small_plan_sizes": None,
    "close_early": False,
    "closing_rules": None,
    "combine": False,
    "lean_prompt": False,
}


def settings(rules_yaml: dict | None) -> dict:
    got = dict((rules_yaml or {}).get("mining") or {})
    out = {**DEFAULTS, **got}
    if out["schedule"] not in SCHEDULES:
        raise ValueError(f"mining.schedule must be one of {SCHEDULES}, got {out['schedule']!r}")
    return out


def planned(tour: list[dict]) -> list[dict]:
    """The tour's built milestones: not the final pass, not review runs."""
    return [m for m in tour if m.get("kind") not in ("final", "review")]


def touches_earlier(m: dict, flagged: bool) -> bool:
    """Whether milestone ``m`` touches earlier features: the planner's ``interacts_with``
    when the plan has such flags (``flagged``), else any ``depends_on``."""
    return bool(m.get("interacts_with")) if flagged else bool(m.get("depends_on"))


def small(tour: list[dict], mining: dict) -> bool:
    """A plan small enough for the plan pass plus one closing pass: at most
    ``small_plan_milestones`` milestones, or every milestone one of ``small_plan_sizes``."""
    ms = planned(tour)
    n, sizes = mining.get("small_plan_milestones"), mining.get("small_plan_sizes")
    return bool(ms) and (
        (n is not None and len(ms) <= int(n))
        or (bool(sizes) and all(m.get("size") in sizes for m in ms))
    )


def budget(tour: list[dict], mining: dict) -> int | None:
    """The build's rule budget (None: unlimited, as in v2)."""
    per, cap = mining.get("rules_per_milestone"), mining.get("max_rules")
    if per is None and cap is None:
        return None
    total = int(per) * len(planned(tour)) if per is not None else int(cap)
    return min(total, int(cap)) if cap is not None else total


def split(total: int, modes: list[str], n: dict) -> dict[str, int]:
    """``total`` rules over ``modes`` as evenly as possible (earlier modes take the
    remainder), each capped at the miner's ``n[mode]``; modes left with none are dropped."""
    out, k = {}, len(modes)
    for i, m in enumerate(modes):
        share = min(total // k + (1 if i < total % k else 0), int(n.get(m, total)))
        if share > 0:
            out[m] = share
    return out


def allot(
    modes: list[str],
    n: dict,
    mining: dict,
    remaining: int | None,
    later: int = 0,
    closing: bool = False,
    closing_later: bool = False,
    failing: bool = False,
) -> dict[str, int]:
    """Rules to ask the miner for, by mode, in one pass. ``remaining``: the budget left
    (None: unlimited, every mode its ``n``); ``later``: mining passes still to come after
    this one, of which the closing pass (``closing_later``) keeps up to ``closing_rules``
    for itself. Once the budget is spent a pass gets nothing, unless a gate is ``failing``
    in its area (then ``rules_per_milestone``)."""
    if remaining is None:
        return {m: int(n[m]) for m in modes if int(n[m]) > 0}
    remaining = max(0, int(remaining))
    if remaining == 0:
        if not failing:
            return {}
        # past the budget: one pass's worth for this area, shared with nobody
        return split(
            int(mining.get("rules_per_milestone") or sum(int(n[m]) for m in modes)), modes, n
        )
    cap = mining.get("closing_rules")
    if closing:
        total = min(remaining, int(cap)) if cap is not None else remaining
    else:
        reserve = closing_later and cap is not None
        keep = min(remaining, int(cap)) if reserve else 0  # stored-shape: reserve: cap is set
        total = -(-(remaining - keep) // max(1, later - int(reserve) + 1))  # ceil
    return split(total, modes, n)


def points(tour: list[dict], ruleset: dict, mining: dict) -> list[dict]:
    """The build's mine points, in order: ``{"point", "after", "modes", "validate"}``.
    ``after`` is the milestone whose kept code the pass runs against (None: before
    building); ``validate``: finish (c)/(d) for waiting plan-pass candidates there. The
    last point of a planned schedule is the closing pass (``closing``: true)."""
    ms = planned(tour)
    if not ms:
        return []
    if mining.get("schedule") != "planned":
        return [
            {
                "point": f"after:{m['id']}",
                "after": m["id"],
                "validate": False,
                "modes": list(
                    (ruleset.get("first") if i == 0 else ruleset.get("after_milestone")) or []
                ),
            }
            for i, m in enumerate(ms)
        ]
    flagged = any("interacts_with" in m for m in ms)
    end = list(mining.get("end_modes") or [])
    out = [
        {
            "point": "plan",
            "after": None,
            "validate": False,
            "modes": list(mining.get("plan_modes") or []),
        },
        {"point": f"after:{ms[0]['id']}", "after": ms[0]["id"], "validate": True, "modes": []},
    ]
    if small(tour, mining):
        # only the plan pass and one closing pass, interactions and edges together
        modes = ["interactions"] + [x for x in end if x != "interactions"]
        if len(ms) == 1:
            out[1].update(modes=modes, closing=True)
        else:
            out.append(
                {
                    "point": f"after:{ms[-1]['id']}",
                    "after": ms[-1]["id"],
                    "validate": False,
                    "modes": modes,
                    "closing": True,
                }
            )
        return out
    for i, m in enumerate(ms[1:], 1):
        modes = ["interactions"] if touches_earlier(m, flagged) else []
        last = i == len(ms) - 1
        if last:
            modes += [x for x in end if x not in modes]
        if modes:
            out.append(
                {
                    "point": f"after:{m['id']}",
                    "after": m["id"],
                    "validate": False,
                    "modes": modes,
                    **({"closing": True} if last else {}),
                }
            )
    if len(ms) == 1 and end:
        out[1].update(modes=end, closing=True)
    return out


def point_after(pts: list[dict], milestone: str) -> dict | None:
    return next((p for p in pts if p["after"] == milestone), None)
