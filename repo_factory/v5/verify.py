"""Stages 3-4: the fresh verifier (docs/design/build-v5.md §3.4).

A new full-tools session works on a read-write copy of the built repo (at /work/product)
with the spec, the merged claims and the lessons, and writes its exam outside it (EX-PLAN
§3.1, at /work/exam): ``_verify/driver.py``, one scenario per claim under
``_verify/scenarios/`` and ``_verify/findings.json``. The harness then loads the scenarios
into the ``RuleStore`` and runs them itself (``exam.run``), on a pristine copy of the build,
twice in opposite orders (``validate.validate_runs``): flaky or broken scenarios are retired,
and the harness's run, not the verifier's word, is the record of pass and fail.

Held split: 30% of the *passing* scenarios become ``held`` (by hash): never in the exam a
repair session sees (``exam/visible``), regression guards the repairer can't read. Failing
ones stay visible."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ..rules.model import Rule
from ..rules.store import RuleStore, held_by_hash
from . import settings

VERIFY_DIR = "_verify"
SKIP = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    "transcript.jsonl",
    "stderr.txt",
    "*.pyc",
)
KIND = {"requirement": "requirement", "interaction": "interaction", "edge": "edge", "input": "edge"}
HEADER = re.compile(r"^#\s*(rule|expect|source|claim|flow):\s*(.*)$", re.M)
SCENARIOS = f"{VERIFY_DIR}/scenarios"  # the harness runs scenarios where the verifier did
FLOW_ID = re.compile(r"\bF\d+\b", re.I)


def flows_of(entry: dict, h: dict) -> list[str]:
    """The SCOPE.md flows a scenario walks (``# flow: F3`` or findings.json ``flow``)."""
    raw = " ".join(str(x) for x in (h.get("flow"), entry.get("flow"), entry.get("flows")) if x)
    return sorted({m.upper() for m in FLOW_ID.findall(raw)}, key=lambda f: int(f[1:]))


def copy_repo(src: Path, dest: Path) -> Path:
    """A copy of a repo without its git history, venvs and caches."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=SKIP, symlinks=True)
    return dest


def prepare(built: Path, dest: Path, spec: str, claims: dict, exam_ws: Path | None = None) -> Path:
    """The verifier's workspace: a copy of the build (bound at /work/product), and its exam
    workspace ``exam_ws`` (bound at /work/exam, EX-PLAN §3.1) with ``_verify/`` holding the
    spec and the claims. Without ``exam_ws`` (a round of the old layout) ``_verify/`` goes
    inside the copy. Returns the exam workspace."""
    ws = copy_repo(built, dest)
    shutil.rmtree(ws / VERIFY_DIR, ignore_errors=True)
    if exam_ws is not None:
        ws = Path(exam_ws)
        ws.mkdir(parents=True, exist_ok=True)
    v = ws / VERIFY_DIR
    if v.exists():
        shutil.rmtree(v)
    (v / "scenarios").mkdir(parents=True)
    (v / "__init__.py").write_text("")
    (v / "spec.md").write_text(spec)
    (v / "claims.json").write_text(
        json.dumps({k: claims.get(k) or [] for k in ("claims", "ambiguities")}, indent=1)
    )
    return ws


def load_findings(ws: Path) -> dict:
    """``_verify/findings.json``, tolerant: missing or broken gives empty lists (the
    scenario files on disk are still picked up by ``scenario_entries``)."""
    p = Path(ws) / VERIFY_DIR / "findings.json"
    try:
        obj = json.loads(p.read_text())
    except (OSError, ValueError):
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    return {
        "scenarios": [s for s in obj.get("scenarios") or [] if isinstance(s, dict)],
        "blocked": [b for b in obj.get("blocked") or [] if isinstance(b, dict)],
        "ambiguities": [a for a in obj.get("ambiguities") or [] if isinstance(a, dict)],
        "settings": obj.get("settings") if isinstance(obj.get("settings"), dict) else {},
        "self_estimate": obj.get("self_estimate")
        if isinstance(obj.get("self_estimate"), dict)
        else {},
    }


def headers(code: str) -> dict:
    return {k: v.strip() for k, v in HEADER.findall(code)}


def scenario_entries(ws: Path, findings: dict) -> list[dict]:
    """Every scenario file the verifier wrote, with its findings.json entry where there is
    one (files findings.json doesn't list are included; entries without a file are not)."""
    sdir = Path(ws) / VERIFY_DIR / "scenarios"
    by_file = {}
    for s in findings.get("scenarios") or []:
        f = str(s.get("file") or "")
        if f:
            by_file[Path(f).name] = s
    out = []
    for p in sorted(sdir.glob("test_*.py")) if sdir.is_dir() else []:
        s = dict(by_file.get(p.name) or {})
        code = p.read_text(errors="replace")
        h = headers(code)
        s.setdefault("id", p.stem.removeprefix("test_"))
        s.setdefault("claim", h.get("claim") or "explore")
        s["file"] = f"{VERIFY_DIR}/scenarios/{p.name}"
        s["code"] = code
        s["headers"] = h
        out.append(s)
    return out


def to_rule(entry: dict, claims_by_id: dict) -> Rule:
    c = claims_by_id.get(str(entry.get("claim"))) or {}
    h = entry.get("headers") or {}
    title = h.get("rule") or (
        f"{c.get('situation', '')}: {c.get('action', '')}".strip(": ") if c else entry["id"]
    )
    return Rule(
        id="",
        title=title[:200],
        kind=KIND.get(c.get("kind"), "edge"),
        source=c.get("quote") or h.get("source") or "",
        code=entry["code"],
        expect=c.get("expect") or h.get("expect") or str(entry.get("expected") or ""),
        source_ref="spec" if c else "verifier",
        origin={
            "mode": "v5",
            "scenario": entry["id"],
            "file": entry["file"],
            "claim": str(entry.get("claim") or "explore"),
            "sets": entry.get("sets") if entry.get("sets") is not None else c.get("sets", []),
            "lessons": entry.get("lessons") or c.get("lessons") or [],
            "basis": c.get("basis") or "explore",
            "verifier_status": entry.get("status"),
            **({"pins": settings.pinned(entry["code"])} if settings.pinned(entry["code"]) else {}),
            **({"flows": flows_of(entry, h)} if flows_of(entry, h) else {}),
            **({"incomplete": True} if entry.get("class") == "incomplete" or h.get("flow") else {}),
            **{
                k: entry[k]
                for k in ("repro", "observed", "expected", "suspect", "scope")
                if entry.get(k)
            },
        },
    )


def import_scenarios(
    store: RuleStore, ws: Path, findings: dict, claims: dict, by: str = "verifier"
) -> list[Rule]:
    """Add the scenarios not yet in the store (by scenario id and code digest); a scenario
    whose code changed since gets a new version (``RuleStore.repair``). Returns the rules
    added or changed (status ``candidate``, visible: the held split comes after the run)."""
    claims_by_id = {c["id"]: c for c in claims.get("claims") or []}
    known = {
        r.origin.get("scenario"): r for r in store.rules.values() if r.origin.get("mode") == "v5"
    }
    out = []
    for e in scenario_entries(ws, findings):
        r = to_rule(e, claims_by_id)
        old = known.get(e["id"])
        if old is not None and old.origin.get("pins") and not settings.pinned(r.code):
            r.code = settings.pin_code(r.code, old.origin["pins"])  # the store's pin stays
            r.origin["pins"] = dict(old.origin["pins"])
        if old is None:
            if store.duplicate_of(r):
                continue
            out.append(store.add(r, by=by, visibility="visible"))
        elif old.digest() != r.digest():
            old.origin.update({k: v for k, v in r.origin.items() if v})
            out.append(store.repair(old.id, r.code, "the verifier rewrote it", by=by))
    return out


def helpers_dir(ws: Path, dest: Path) -> Path | None:
    """The verifier's helpers (``_verify/`` without scenarios, findings and the spec), as
    the overlay every scenario run gets at ``_verify/``."""
    src = Path(ws) / VERIFY_DIR
    if not src.is_dir():
        return None
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(
            "scenarios", "findings.json", "claims.json", "spec.md", "__pycache__", "*.pyc"
        ),
    )
    (dest / "__init__.py").touch()
    return dest


def validate(store: RuleStore, rules: list[Rule], spec: str, run) -> dict:
    """``validate.validate_runs``: two runs in opposite orders; broken or flaky scenarios
    are retired, the rest become active. Returns ``{"active", "rejected", "results"}``."""
    from ..rules.miner import Context
    from ..rules.validate import validate_runs

    if not rules:
        return {"active": [], "rejected": {}, "results": {}}
    return validate_runs(store, rules, Context(spec=spec), run, by="v5-validate")


def held_split(store: RuleStore, passing: list[str], fraction: float) -> list[str]:
    """``fraction`` of the passing scenarios (by hash) become held. Returns the held ids."""
    held = []
    with store.lock:
        for rid in passing:
            r = store.rules[rid]
            if r.visibility == "visible" and held_by_hash(r, fraction):
                r.visibility = "held"
                store._log(
                    id=rid,
                    version=r.version,
                    by="v5-held",
                    frm_visibility="visible",
                    to_visibility="held",
                    reason="held split (passing scenario)",
                )
                held.append(rid)
        store.save()
    for rid in held:  # rule_status events: the fold counts held
        store._changed(store.rules[rid], "active", "active", "held split")
    return held


def replace_held(store: RuleStore, promoted: str, passing: list[str]) -> str | None:
    """A held scenario that regressed is promoted to visible (``RuleStore.promote``) and
    another passing visible scenario takes its place in the held set."""
    if store.promote(promoted, "regressed after a repair", by="v5-accept"):
        store._changed(store.rules[promoted], "active", "active", "promoted: regressed")
    for rid in passing:
        r = store.rules.get(rid)
        if r is not None and r.visibility == "visible" and r.status == "active" and rid != promoted:
            with store.lock:
                r.visibility = "held"
                store._log(
                    id=rid,
                    version=r.version,
                    by="v5-held",
                    frm_visibility="visible",
                    to_visibility="held",
                    reason=f"replaces promoted {promoted}",
                )
                store.save()
            store._changed(r, "active", "active", f"held: replaces {promoted}")
            return rid
    return None


def pin(
    store: RuleStore, rule_id: str, pins: dict[str, str], why: str, by: str = "v5-setting"
) -> bool:
    """Pin a scenario to setting values (``settings.pin_code``; ``{}`` unpins): a new
    version of its code that stays ``active`` (the check didn't change, only the
    configuration it runs under). Returns False if nothing changed."""
    r = store.rules[rule_id]
    code = settings.pin_code(r.code, pins)
    if code == r.code:
        return False
    with store.lock:
        r.code, r.version = code, r.version + 1
        r.origin["pins"] = dict(pins)
        store._log(id=rule_id, version=r.version, by=by, reason=f"pinned {pins}: {why}")
        store.save()
    store._changed(r, r.status, r.status, f"pinned {pins}: {why}")
    return True
