"""Stage 2: claims and ambiguities (docs/design/build-v5.md §3.3).

``claims.samples`` no-tools calls (one, the default) with the same prompt read the spec (and
the injected lessons) and list claims and ambiguities; no code, so the reading isn't anchored
on what the builder did. With more than one sample, one cheap call deduplicates the union;
each merged item keeps ``sets`` (which samples produced it). Two samples existed for the
retired p_hidden estimate's capture-recapture term (AU1 #5)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from . import settings
from .common import Budget, BudgetExceeded, ask_json, parallel, prompt

BASES = ("spec_explicit", "spec_implied", "convention", "silent")
KINDS = ("requirement", "interaction", "edge", "input")


# -- lessons (§5.2) ---------------------------------------------------------------------------
def load_lessons(path: Path | None, pack: str | None = None) -> list[dict]:
    """The lessons a build may see: status ``active`` or ``probation``, not born on ``pack``
    (so every dev score is leave-one-pack-out), sorted by hit rate. Track E's
    ``lessons.store.for_build`` is used when it exists."""
    if path is None or not Path(path).exists():
        return []
    try:  # track E
        from ..lessons.store import for_build

        return list(for_build(pack, path=Path(path)))
    except (ImportError, TypeError):
        pass
    data = yaml.safe_load(Path(path).read_text()) or {}
    out = [
        x
        for x in data.get("lessons") or []
        if x.get("status") in ("active", "probation")
        and (pack is None or (x.get("born") or {}).get("pack") != pack)
    ]
    return sorted(out, key=lambda x: -float(x.get("hit_rate") or 0.0))


def lessons_md(lessons: list[dict], applies_to: str) -> str:
    mine = [x for x in lessons if x.get("applies_to", "claims") == applies_to]
    if not mine:
        return ""
    L = ["Lessons from earlier builds of other specs (most useful first):"]
    for x in mine:
        s = x.get("stats") or {}
        rate = (
            f" (found a defect in {s.get('caught', 0)} of {s.get('exposures', 0)} builds)"
            if s.get("exposures")
            else ""
        )
        L.append(f"- {x['id']}{rate}: {x['text']}")
    return "\n".join(L)


# -- one sample ----------------------------------------------------------------------------------
def clean(obj: dict, max_claims: int, max_ambiguities: int) -> dict:
    """The sample's claims and ambiguities, normalised; ``silent`` claims become ambiguities."""
    claims, amb = [], []
    for c in (obj.get("claims") or [])[: max_claims * 2]:
        if not isinstance(c, dict) or not (c.get("situation") or c.get("expect")):
            continue
        basis = c.get("basis") if c.get("basis") in BASES else "spec_implied"
        if basis == "silent":
            amb.append(
                {
                    "question": f"{c.get('situation', '')}: {c.get('action', '')}".strip(": "),
                    "literal": "",
                    "expected": c.get("expect", ""),
                    "recommend": "expected",
                    "stakes": "low",
                    "kind": "complex",
                }
            )
            continue
        claims.append(
            {
                "situation": str(c.get("situation") or ""),
                "action": str(c.get("action") or ""),
                "expect": str(c.get("expect") or ""),
                "basis": basis,
                "quote": str(c.get("quote") or ""),
                "kind": c.get("kind") if c.get("kind") in KINDS else "requirement",
                "lessons": [str(x) for x in c.get("lessons") or []],
            }
        )
    for a in obj.get("ambiguities") or []:
        if isinstance(a, dict) and a.get("question"):
            amb.append(ambiguity(a))
    return {"claims": claims[:max_claims], "ambiguities": amb[:max_ambiguities]}


def ambiguity(a: dict) -> dict:
    """One ambiguity, normalised, with its ``kind`` (Addendum A): ``binary`` with a usable
    ``setting`` (``values`` = [literal value, expected value], ``default`` = the recommended
    reading's), else ``complex``."""
    rec = a.get("recommend") if a.get("recommend") in ("literal", "expected") else "expected"
    out = {
        "question": str(a["question"]),
        "literal": str(a.get("literal") or ""),
        "expected": str(a.get("expected") or ""),
        "recommend": rec,
        "stakes": "high" if a.get("stakes") == "high" else "low",
        **({"why_high": str(a["why_high"])} if a.get("why_high") else {}),
    }
    st = None
    if a.get("kind") == "binary":
        raw = a.get("setting") if isinstance(a.get("setting"), dict) else {}
        vals = [settings.norm_value(v) for v in raw.get("values") or []]
        st = settings.clean(raw, vals[0 if rec == "literal" else 1] if len(vals) == 2 else None)
    out["kind"] = "binary" if st else "complex"
    if st:
        out["setting"] = st
    return out


def sample(
    llm, policy: Path, spec: str, lessons: list[dict], cfg: dict, budget: Budget | None = None
) -> dict:
    """One claim list. ``cfg["prompt"]``: the prompt (``claims``; the simple flow's Plan
    writes its checklist with ``checklist``, in ``cfg["stage"]``)."""
    name, stage = cfg.get("prompt") or "claims", cfg.get("stage") or "claims"
    text = prompt(
        policy,
        name,
        spec=spec.strip(),
        lessons=lessons_md(lessons, "claims"),
        max_claims=str(cfg.get("max_claims", 60)),
        max_ambiguities=str(cfg.get("max_ambiguities", 8)),
    )
    obj = ask_json(llm, "claims", text, budget, stage)
    return clean(obj, int(cfg.get("max_claims", 60)), int(cfg.get("max_ambiguities", 8)))


# -- merging -------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", (text or "").lower())).strip()


def _key(item: dict, kind: str) -> str:
    if kind == "claims":
        return _norm(" | ".join((item["situation"], item["action"], item["expect"])))
    return _norm(item["question"])


def _items_md(samples: list[dict]) -> str:
    L = []
    for i, s in enumerate(samples, 1):
        for j, c in enumerate(s["claims"], 1):
            L.append(f"- claim {i}:c{j}: when {c['situation']}, {c['action']} -> {c['expect']}")
        for j, a in enumerate(s["ambiguities"], 1):
            L.append(f"- ambiguity {i}:a{j}: {a['question']}")
    return "\n".join(L)


def merge(
    samples: list[dict], llm=None, policy: Path | None = None, budget: Budget | None = None
) -> dict:
    """The union of the samples, deduplicated: by one call that groups duplicates (when
    ``llm`` is given), and always by identical normalised text. Ids are renumbered
    (``c1``.., ``a1``..); each item gets ``sets``. A claim's ``lessons`` are the union."""
    groups: dict[str, list[list[str]]] = {"claims": [], "ambiguities": []}
    how = "text"
    # One sample has nothing to merge across: the dedupe call is skipped (identical text
    # is still merged).
    if (
        llm is not None
        and policy is not None
        and len(samples) > 1
        and sum(len(s["claims"]) for s in samples)
    ):
        try:
            obj = ask_json(
                llm,
                "dedupe",
                prompt(policy, "claims", "dedupe", items=_items_md(samples)),
                budget,
                "claims",
            )
            for k in groups:
                groups[k] = [[str(x) for x in g] for g in obj.get(k) or [] if isinstance(g, list)]
            how = "llm" if obj else "text"
        except BudgetExceeded:
            pass
    out = {}
    for kind, prefix in (("claims", "c"), ("ambiguities", "a")):
        ref = {}  # "1:c3" -> the item
        for i, s in enumerate(samples, 1):
            for j, item in enumerate(s[kind], 1):
                ref[f"{i}:{prefix}{j}"] = item
        parent = {k: k for k in ref}

        def find(k):
            while parent[k] != k:
                k = parent[k]
            return k

        for g in groups[kind]:
            g = [k for k in g if k in ref]
            for k in g[1:]:
                parent[find(k)] = find(g[0])
        by_text: dict[str, str] = {}
        for k in ref:
            t = _key(ref[k], kind)
            if t in by_text:
                parent[find(k)] = find(by_text[t])
            else:
                by_text[t] = k
        merged: dict[str, dict] = {}
        for k in ref:  # sample order, item order
            root = find(k)
            sets = {int(k.split(":")[0])}
            if root not in merged:
                merged[root] = {**ref[root], "sets": set()}
            m = merged[root]
            m["sets"] |= sets
            if kind == "claims":
                m["lessons"] = sorted(
                    set(m.get("lessons") or []) | set(ref[k].get("lessons") or [])
                )
                if ref[k].get("basis") == "spec_explicit":
                    m["basis"] = "spec_explicit"
            elif ref[k].get("setting") and not m.get("setting"):
                m.update(kind="binary", setting=ref[k]["setting"])  # one sample saw a switch
        items = []
        for n, m in enumerate(merged.values(), 1):
            items.append(
                {
                    "id": f"{prefix}{n}",
                    **{k: v for k, v in m.items() if k != "sets"},
                    "sets": sorted(m["sets"]),
                }
            )
        out[kind] = items
    out["samples"] = len(samples)
    out["dedupe"] = how
    return out


def run(
    llm, policy: Path, spec: str, lessons: list[dict], cfg: dict, budget: Budget | None = None
) -> dict:
    """Stage 2: ``cfg['samples']`` independent samples in parallel (at least one), then the
    merge."""
    n = max(1, int(cfg.get("samples", 1)))
    samples = parallel([lambda: sample(llm, policy, spec, lessons, cfg, budget) for _ in range(n)])
    return merge(samples, llm, policy, budget)


def write(path: Path, claims: dict) -> None:
    Path(path).write_text(json.dumps(claims, indent=1))
