"""Stage 5: the oracle panel and the conflict protocol (docs/design/build-v5.md §3.5).

- ``panel``: three independent no-tools calls on the *failing* scenarios' claims in prose
  (no test code, no build code). Each claim gets ``required | not_required | silent`` per
  vote; >= ``defect_min`` required is a **defect**, >= 2 not_required is **retired**, anything
  else is an **interpretation**. One call per vote covers every failing claim. Each vote also
  says whether the claim is one side of a *binary business policy* (Addendum A); when at
  least 2 votes say so, the class is **setting** whatever the required count (except a
  claim the spec states in so many words, 3 of 3 required: rank 2 is never a policy).
- ``pair``: for a repair that turned X green and Y red, three votes on "can a correct system
  satisfy both; if not, which does the spec require?" -> ``compatible | x_wins | y_wins |
  undecidable`` (the majority; no majority is ``undecidable``), and ``setting`` when at least
  2 votes call the pair the two sides of a binary policy. ``accept.resolve`` then applies
  the authority order.

Addendum A retired the interpretation vote ("which reading would the spec's author
expect?"): binary items are settings with a documented default, complex ones keep the
build's reading, so nothing is left to vote on."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import settings as settings_mod
from .common import Budget, ask_json, parallel, prompt

VOTES = ("required", "not_required", "silent")
PAIR = ("compatible", "x_wins", "y_wins", "undecidable")


def prose(item: dict) -> str:
    """A claim in prose: situation, action, expected outcome (and the spec quote it cites;
    never code). ``item`` is a claim, or a rule's ``origin`` plus title/expect."""
    if item.get("prose"):
        return item["prose"]
    sit = item.get("situation") or item.get("title") or ""
    act = item.get("action") or ""
    exp = item.get("expect") or item.get("expected") or ""
    s = f"Situation: {sit}\n" + (f"Action: {act}\n" if act else "") + f"Expected: {exp}"
    if item.get("quote"):
        s += f'\nCites: "{item["quote"]}"'
    return s


def answers_md(answers: dict | None) -> str:
    if not answers:
        return "(none)"
    return "\n".join(f"- {k}: {v}" for k, v in answers.items() if v)


def known_md(known: list[dict] | None) -> str:
    if not known:
        return "(none)"
    return "\n".join(
        f"- `{s['key']}`: values {', '.join(s['values'])}"
        + (f" ({s['question']})" if s.get("question") else "")
        for s in known
    )


def policy_of(replies: list, min_votes: int = 2) -> dict | None:
    """The binary policy ``min_votes`` or more votes saw (``replies``: each vote's
    ``policy`` or None), normalised: the most common key, its first well-formed proposal,
    ``claim_value`` among its values. None if fewer votes saw one."""
    props = []
    for p in replies:
        if not isinstance(p, dict):
            continue
        st = settings_mod.clean(p)
        if st:
            cv = settings_mod.norm_value(p.get("claim_value"))
            props.append(
                {
                    **st,
                    "claim_value": cv if cv in st["values"] else st["values"][0],
                    **({"question": str(p["question"])} if p.get("question") else {}),
                }
            )
    if len(props) < min_votes:
        return None
    key = Counter(p["key"] for p in props).most_common(1)[0][0]
    best = next(p for p in props if p["key"] == key)
    return {**best, "votes": len(props)}


def panel(
    llm,
    policy: Path,
    spec: str,
    items: dict[str, dict],
    answers: dict | None = None,
    votes: int = 3,
    defect_min: int = 2,
    budget: Budget | None = None,
    known: list[dict] | None = None,
) -> dict:
    """``items``: id -> claim. Returns id -> ``{"votes": [...], "required", "not_required",
    "silent", "class": defect|retired|interpretation|setting, "unanimous", "quotes",
    "setting"}`` (``setting``: the binary policy the votes saw, or None). ``known``: the
    settings proposed so far (the claims' binary ambiguities), so votes reuse their keys."""
    if not items:
        return {}
    text = prompt(
        policy,
        "oracle",
        spec=spec.strip(),
        answers=answers_md(answers),
        settings=known_md(known),
        claims="\n\n".join(f"### {i}\n{prose(c)}" for i, c in items.items()),
    )
    replies = parallel(
        [lambda: ask_json(llm, "oracle", text, budget, "oracle") for _ in range(votes)]
    )
    out = {}
    for i in items:
        vs, quotes, pol = [], [], []
        for r in replies:
            v = (r.get("results") or {}).get(i) or {}
            vote = v.get("vote") if v.get("vote") in VOTES else "silent"
            vs.append(vote)
            pol.append(v.get("policy"))
            if v.get("quote") or v.get("reason"):
                quotes.append(str(v.get("quote") or v.get("reason")))
        c = Counter(vs)
        stated = items[i].get("basis") == "spec_explicit" and c["required"] == len(vs) >= 3
        st = None if stated else policy_of(pol)
        cls = (
            "setting"
            if st
            else "defect"
            if c["required"] >= defect_min
            else "retired"
            if c["not_required"] >= 2
            else "interpretation"
        )
        out[i] = {
            "votes": vs,
            "required": c["required"],
            "not_required": c["not_required"],
            "silent": c["silent"],
            "class": cls,
            "unanimous": c["required"] == len(vs),
            "quotes": quotes[:3],
            "setting": st,
        }
    return out


def pair(
    llm,
    policy: Path,
    spec: str,
    x: dict,
    y: dict,
    answers: dict | None = None,
    votes: int = 3,
    budget: Budget | None = None,
) -> dict:
    """``{"verdict", "votes", "reasons", "setting"}``: the majority of ``votes``; none is
    ``undecidable``. ``setting``: the binary policy >= 2 votes saw in the pair (X's side is
    its ``claim_value``), or None."""
    text = prompt(
        policy, "pair", spec=spec.strip(), answers=answers_md(answers), x=prose(x), y=prose(y)
    )
    replies = parallel(
        [lambda: ask_json(llm, "pair", text, budget, "oracle") for _ in range(votes)]
    )
    vs = [r.get("verdict") if r.get("verdict") in PAIR else "undecidable" for r in replies]
    top, n = Counter(vs).most_common(1)[0]
    return {
        "verdict": top if n * 2 > len(vs) else "undecidable",
        "votes": vs,
        "reasons": [str(r.get("reason") or "") for r in replies][:3],
        "setting": policy_of([r.get("policy") for r in replies]),
    }
