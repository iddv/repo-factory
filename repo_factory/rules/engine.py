"""The rules engine for one build: store + miner + validation + arbitration + scoring.

The orchestrator (policy v2 tour) calls:

- ``after_milestone(code_dir, digest, milestone, built)`` when the tour moves past a
  milestone: mine (``requirements`` after the first milestone, ``interactions`` after
  every later one), validate, score the kept candidate on every due active rule, and
  arbitrate the failures;
- ``score(code_dir, digest, due)`` for every candidate (continuous scoring; cached);
- ``review(...)`` (``loop.review_loop``) after the last milestone.

Settings come from the policy's ``rules.yaml`` under ``ruleset:`` (``DEFAULTS``)."""

from __future__ import annotations

import threading
from pathlib import Path

from . import arbiter as arb
from .miner import Context, Miner
from .schedule import DEFAULTS as MINING_DEFAULTS
from .score import run_rules, summarize
from .store import RuleStore
from .validate import validate, validate_runs

MODE_OF = {
    "requirement": "requirements",
    "interaction": "interactions",
    "edge": "edges",
    "regression": "edges",
}

DEFAULTS = {
    "enabled": True,
    "first": ["requirements"],  # mined after the first milestone
    "after_milestone": ["interactions"],  # mined after every later milestone
    "review": ["interactions", "edges"],  # mined in each review round
    "review_rounds": 3,  # closing-loop cap
    "held_fraction": 0.25,  # share of rules never shown to workers
    "coverage_weight": 0.6,  # share of the coverage term from the rule pass rate
    "gate_min_rate": None,  # milestone rule slice; None: tour gate_min_coverage
    "n": {},  # rules per mining call, by mode
}


def settings(rules_yaml: dict | None) -> dict:
    got = dict((rules_yaml or {}).get("ruleset") or {})
    return {**DEFAULTS, **got}


class RulesEngine:
    def __init__(
        self,
        store: RuleStore,
        llm,
        ctx: Context,
        requirements: list[str] | None = None,
        cfg: dict | None = None,
        emit=None,
        cache: Path | None = None,
    ):
        self.store, self.llm, self.base = store, llm, ctx
        self.requirements = list(requirements or [])
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.emit = emit or (lambda *a, **k: None)
        self.cache = Path(cache) if cache else store.root / "results"
        self.miner = Miner(llm, self.cfg.get("n") or {})
        self.mining = dict(MINING_DEFAULTS)  # ``mining:`` settings (rules.schedule)
        self.to_replace: list[str] = []  # promoted held rules awaiting a replacement
        self.spent = 0  # rules mined so far (the budget, schedule.allot)
        # Build v3 §4: the arbiter may say ``design_flaw`` (only with the controller on);
        # ``adr()`` returns the current ADR text it is validated against.
        self.design_flaw = False
        self.adr = lambda: None
        self.lock = threading.Lock()
        store.on_change = self._status_event

    # -- plumbing ------------------------------------------------------------------
    def _status_event(self, r, old, new, reason) -> None:
        self.emit(
            "rule_status",
            id=r.id,
            kind=r.kind,
            title=r.title,
            milestones=r.milestones,
            visibility=r.visibility,
            version=r.version,
            frm=old,
            to=new,
            reason=reason[:300],
        )

    def venv(self, code_dir: Path) -> Path:
        from ..harness.venv import candidate_venv

        return candidate_venv(Path(code_dir), self.requirements)[0]

    def runner(self, code_dir: Path, digest: str | None = None):
        venv = self.venv(code_dir)

        def run(rules, reverse=False):
            return run_rules(
                code_dir,
                rules,
                venv,
                reverse=reverse,
                cache=self.cache if digest else None,
                digest=digest,
            )

        return run

    def context(
        self, code_dir: Path | None, milestone: str | None = None, built=(), lean: bool = False
    ) -> Context:
        """``lean`` (``mining.lean_prompt``): the code digest is the source files only, those
        of ``milestone`` and the built milestones (the plan's ``files``) first."""
        from .tree import artifact_text, source_text

        b = self.base
        code = ""
        if code_dir and lean:
            files = {m["id"]: m.get("files") or [] for m in b.milestones}
            first = [f for mid in [milestone, *reversed(list(built))] for f in files.get(mid, [])]
            code = source_text(code_dir, first)
        if code_dir and code in ("", "(empty)"):
            code = artifact_text(code_dir)
        return Context(
            spec=b.spec,
            answers=b.answers,
            questions=b.questions,
            interface=b.interface,
            milestones=b.milestones,
            code=code,
            milestone=milestone,
            built=list(built),
        )

    # -- steps -----------------------------------------------------------------------
    def mine(
        self,
        mode,
        code_dir: Path | None,
        digest: str | None = None,
        milestone: str | None = None,
        built=(),
        embargo: bool = False,
        replaces=None,
        n: int | dict | None = None,
    ) -> dict:
        """Mine ``mode`` against the code (None: before any code exists, the plan pass),
        add the proposals as candidates and validate them ((c)/(d) wait for code).
        ``embargo``: a background pass; its rules wait for ``store.admit``. ``replaces``:
        mine one held rule in the same area as that promoted rule. ``mode``: a list of code
        modes for one combined call (``mining.combine``); ``n``: the rules to ask for (the
        pass's allotment, ``schedule.allot``). Emits ``rules_mined``."""
        lean = bool(self.mining.get("lean_prompt"))
        ctx = self.context(code_dir, milestone, built, lean=lean)
        usd0 = getattr(self.llm, "usd", 0.0)
        with self.store.lock:
            existing = list(self.store.rules.values())
        proposed = self.miner.mine(mode, ctx, existing, replaces=replaces, n=n, lean=lean)
        added, dupes = [], 0
        for r in proposed:
            if self.store.duplicate_of(r):
                dupes += 1
                continue
            added.append(
                self.store.add(
                    r, embargo=embargo, visibility="held" if replaces is not None else None
                )
            )
        if replaces is None:
            with self.lock:
                self.spent += len(added)
        res = validate(
            self.store,
            added,
            ctx,
            self.llm,
            self.runner(code_dir, digest) if code_dir else None,
            root=Path(code_dir) if code_dir else None,
        )
        from .miner import mode_tag

        out = {
            "mode": mode_tag(mode),
            "after": milestone,
            "proposed": len(proposed),
            "duplicates": dupes,
            "active": len(res["active"]),
            "rejected": len(res["rejected"]),
            "rejected_by": _by_check(res["rejected"]),
            "failing_now": sorted(
                i for i, v in res["results"].items() if v.get("status") != "passed"
            ),
            "total_active": len(self.store.active()),
            "usd": round(getattr(self.llm, "usd", 0.0) - usd0, 4),
        }
        if res["pending"]:
            out["pending"] = len(res["pending"])
        if replaces is not None:
            out["replaces"] = replaces.id
        if n is not None:
            out["asked"] = n
        self.emit("rules_mined", **out)
        out["ids"] = [r.id for r in added]
        return out

    def mine_design(
        self,
        items: list[dict],
        code_dir: Path,
        digest: str | None = None,
        milestone: str | None = None,
        built=(),
        batch: int = 10,
        workers: int = 4,
    ) -> dict:
        """Build v4 hardening (V4-D): a rule for each design item (an open gap of the final
        design walk, or a rule the design was walked against; ``core.oneshot``), through the
        miner's path: batches of ``batch`` items, one call each, in parallel; then the same
        validation (a)-(d) on the code as mined rules. They are always **visible**: they are
        the known-critical behaviour, so a failure the arbiter blames on the code goes to
        the repair. They don't count against the mining budget (``spent``). Emits
        ``rules_mined`` (mode ``design``)."""
        import contextvars
        from concurrent.futures import ThreadPoolExecutor

        lean = bool(self.mining.get("lean_prompt"))
        ctx = self.context(code_dir, milestone, built, lean=lean)
        usd0 = getattr(self.llm, "usd", 0.0)
        with self.store.lock:
            existing = list(self.store.rules.values())
        b = max(1, int(batch or 1))
        chunks = [items[i : i + b] for i in range(0, len(items), b)]
        errors = []

        def one(chunk):
            try:
                return self.miner.mine_design(chunk, ctx, existing, lean)
            except Exception as e:  # noqa: BLE001 - one failed batch doesn't stop the rest
                errors.append(f"{type(e).__name__}: {e}"[:300])
                return []

        n = max(1, min(int(workers or 1), len(chunks) or 1))
        with ThreadPoolExecutor(max_workers=n) as ex:
            got = list(ex.map(lambda c: contextvars.copy_context().run(one, c), chunks))
        proposed = [r for g in got for r in g]
        added, dupes = [], 0
        for r in proposed:
            if self.store.duplicate_of(r):
                dupes += 1
                continue
            added.append(self.store.add(r, visibility="visible"))
        from . import validate as V  # the module's text, so a prompt pack applies

        res = validate(
            self.store,
            added,
            ctx,
            self.llm,
            self.runner(code_dir, digest) if code_dir else None,
            root=Path(code_dir) if code_dir else None,
            note=V.DESIGN_CHECK_SUFFIX,
        )
        out = {
            "mode": "design",
            "after": milestone,
            "items": len(items),
            "proposed": len(proposed),
            "duplicates": dupes,
            "active": len(res["active"]),
            "rejected": len(res["rejected"]),
            "rejected_by": _by_check(res["rejected"]),
            "failing_now": sorted(
                i for i, v in res["results"].items() if v.get("status") != "passed"
            ),
            "total_active": len(self.store.active()),
            "usd": round(getattr(self.llm, "usd", 0.0) - usd0, 4),
        }
        if errors:
            out["error"] = "; ".join(errors)[:300]
        self.emit("rules_mined", **out)
        out["ids"] = [r.id for r in added]
        out["by_item"] = {}
        for r in added:
            out["by_item"].setdefault(r.origin.get("design") or "?", []).append(r.id)
        return out

    def pending(self) -> list:
        """Candidates that passed (a) and (b) and wait for code ((c)/(d)): the plan pass."""
        return [
            r
            for r in self.store.select("candidate")
            if r.checks.get("b") == "agree"
            and "c" not in r.checks
            and r.checks.get("d") is not False
        ]

    def validate_pending(
        self, code_dir: Path, digest: str | None = None, embargo: bool = False
    ) -> dict:
        """(c) and (d) for the waiting plan-pass candidates on the first kept code.
        ``embargo``: a background pass; they become usable when it is admitted."""
        todo = self.pending()
        if not todo:
            return {"active": [], "rejected": {}, "results": {}}
        if embargo:
            with self.store.lock:
                self.store.embargo |= {r.id for r in todo}
        return validate_runs(
            self.store,
            todo,
            self.context(code_dir),
            self.runner(code_dir, digest),
            root=Path(code_dir),
        )

    def due(self, built) -> set[str] | None:
        return None if built is None else set(built)

    def score(self, code_dir: Path, digest: str | None = None, built=None) -> dict:
        """Rule id -> outcome over every due active rule (visible and held)."""
        rules = self.store.active(due=self.due(built))
        return self.runner(code_dir, digest)(rules) if rules else {}

    def summary(self, results: dict, built=None, visibility: str | None = None) -> dict:
        rules = self.store.active(visibility, self.due(built))
        s = summarize(results, rules)
        s["key"] = self.store.key(rules)
        return s

    def arbitrate(
        self,
        code_dir: Path,
        results: dict,
        digest: str | None = None,
        built=None,
        embargo: bool = False,
        point: str | None = None,
    ) -> dict:
        """Arbitrate the failing active rules. With ``mining.promote_held``, a held rule the
        arbiter blames on the code is promoted to visible (``rule_promoted``) and queued for
        a held replacement (``replace_promoted``). ``embargo``: a background pass; repaired
        and promoted rules wait for ``store.admit`` like new ones."""
        failing = [
            self.store.rules[i]
            for i, v in results.items()
            if v.get("status") != "passed"
            and i in self.store.rules
            and self.store.rules[i].status == "active"
        ]
        ctx = self.context(code_dir, None, built or ())
        run = self.runner(code_dir, digest)

        def revalidate(ids):
            validate(
                self.store,
                [self.store.rules[i] for i in ids],
                ctx,
                self.llm,
                run,
                by="arbiter",
                agree=False,
                root=Path(code_dir),
            )

        out = arb.run(
            self.store,
            self.llm,
            failing,
            results,
            ctx,
            revalidate,
            **({"design_flaw": True, "adr": self.adr()} if self.design_flaw else {}),
        )
        if embargo and out.get("repaired"):
            with self.store.lock:
                self.store.embargo |= set(out["repaired"])
        out["promoted"] = []
        if self.mining.get("promote_held"):
            # A held rule that found a design flaw is promoted too: the design change that
            # follows tells the workers about it anyway.
            for rid in out["code_wrong"] + list(out.get("design_flaw") or []):
                r = self.store.rules[rid]
                kind = "design flaw" if rid in (out.get("design_flaw") or []) else "code wrong"
                why = next(
                    (
                        v.get("reason", "")
                        for v in reversed(r.verdicts)
                        if v.get("verdict") in ("code_wrong", "design_flaw")
                    ),
                    "",
                )
                if self.store.promote(rid, f"arbiter: {kind}: {why}", embargo=embargo):
                    out["promoted"].append(rid)
                    with self.lock:
                        self.to_replace.append(rid)
                    self.emit(
                        "rule_promoted",
                        id=rid,
                        kind=r.kind,
                        title=r.title,
                        milestones=r.milestones,
                        version=r.version,
                        reason=why[:300],
                        point=point,
                    )
        passing = [i for i, v in results.items() if v.get("status") == "passed"]
        out["fixed"] = self.store.mark_fixed(passing)
        return out

    def replace_promoted(
        self, code_dir: Path, digest: str | None = None, built=(), embargo: bool = False
    ) -> list[dict]:
        """Mine one held replacement for each promoted rule still waiting for one, in the
        same area (mode by the rule's kind, its milestones and source in the prompt)."""
        with self.lock:
            todo, self.to_replace = self.to_replace, []
        out = []
        for rid in todo:
            r = self.store.rules[rid]
            mode = MODE_OF.get(r.kind, "edges")
            try:
                got = self.mine(
                    mode,
                    code_dir,
                    digest,
                    r.milestones[-1] if r.milestones else None,
                    built,
                    embargo=embargo,
                    replaces=r,
                )
            except Exception as e:  # noqa: BLE001 - the rule set is an aid
                self.emit(
                    "rules_mined", mode=mode, replaces=rid, error=f"{type(e).__name__}: {e}"[:300]
                )
                continue
            out.append({"replaces": rid, "ids": got["ids"], "active": got["active"]})
        return out

    def calls(self, modes: list[str], asked: dict | None) -> list[tuple]:
        """The miner calls of a pass: ``(mode, n)`` each, or one combined call for
        several modes (``mining.combine``). ``asked``: the pass's allotment by mode (None:
        the miner's defaults); a mode allotted nothing is not mined."""
        if asked is not None:
            modes = [m for m in modes if asked.get(m)]
        if len(modes) > 1 and self.mining.get("combine"):
            return [(list(modes), {m: asked[m] for m in modes} if asked else None)]
        return [(m, asked[m] if asked else None) for m in modes]

    def after_milestone(
        self,
        code_dir: Path,
        digest: str | None,
        milestone: str,
        built: list[str],
        first: bool = False,
        modes=None,
        validate_waiting: bool = False,
        embargo: bool = False,
        asked: dict | None = None,
    ) -> dict:
        """Mine ``modes`` (default: ``first`` after milestone 1, ``after_milestone``
        after later ones), validate any waiting plan-pass candidates, score the kept code
        on every due active rule and arbitrate the failures (promoting held rules that
        caught a bug, then mining their replacements). ``embargo``: a background pass.
        ``asked``: rules per mode (the pass's allotment of the budget)."""
        if modes is None:
            modes = self.cfg["first"] if first else self.cfg["after_milestone"]
        validated = (
            self.validate_pending(code_dir, digest, embargo) if validate_waiting else {"active": []}
        )
        mined = [
            self.mine(m, code_dir, digest, milestone, built, embargo=embargo, n=k)
            for m, k in self.calls(list(modes or []), asked)
        ]
        results = self.score(code_dir, digest, built)
        verdicts = self.arbitrate(
            code_dir, results, digest, built, embargo=embargo, point=f"after:{milestone}"
        )
        replaced = self.replace_promoted(code_dir, digest, built, embargo=embargo)
        ids = [i for m in mined for i in m.get("ids", [])] + [i for x in replaced for i in x["ids"]]
        return {
            "mined": mined,
            "validated": validated["active"],
            "results": results,
            "arbitration": verdicts,
            "replaced": replaced,
            "ids": ids,
            "summary": self.summary(results, built),
        }


def _by_check(rejected: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for why in rejected.values():
        k = why[1] if why.startswith("(") and len(why) > 2 else "?"
        out[k] = out.get(k, 0) + 1
    return out
