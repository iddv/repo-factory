"""The rule store: one per build, under the den's cache (never in a workspace).

Layout of ``<root>``:

- ``rules.json``: every rule ever proposed in this build, with its current status.
- ``history.jsonl``: one line per status change (and per repair), append-only.
- ``questions.json``: ambiguities the arbiter found, for the owner.

Workers can never write to it: it lives in the den, and workers run in sandboxed
workspaces outside the den (``orchestrator.work_root``). Only ``export`` puts rule code in
a workspace, and only visible, active rules; held rules leave the store only to the
factory's own pytest runs (``score.run_rules``)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .model import KINDS, PREFIX, STATUSES, Rule

HELD_FRACTION = 0.25


def held_by_hash(rule: Rule, fraction: float) -> bool:
    """Deterministic held-back share: by a hash of the rule's normalised code."""
    if fraction <= 0:
        return False
    h = int(hashlib.sha256(rule.digest().encode()).hexdigest()[:8], 16)
    return (h % 1000) < fraction * 1000


class RuleStore:
    """Thread-safe: every read and write holds ``lock`` (a background mining pass writes
    while the main thread scores and exports).

    ``embargo``: rule ids a background pass added (or repaired, or promoted) that the build
    may not use yet. Reads with ``admitted=True`` (scoring, gates, workspace exports) skip
    them until ``admit``, so a rule joins the *next* gate, never one already under way.
    Without background mining the embargo stays empty and nothing changes."""

    def __init__(self, root: Path, held_fraction: float = HELD_FRACTION):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.held_fraction = held_fraction
        self.rules: dict[str, Rule] = {}
        self.questions: list[dict] = []
        self.embargo: set[str] = set()
        self.lock = threading.RLock()
        self.on_change = None  # fn(rule, old_status, new_status, reason): rule_status events
        p = self.root / "rules.json"
        if p.exists():
            for d in json.loads(p.read_text()):
                r = Rule.from_dict(d)
                self.rules[r.id] = r
        q = self.root / "questions.json"
        if q.exists():
            self.questions = json.loads(q.read_text())

    # -- persistence -------------------------------------------------------------
    def save(self) -> None:
        with self.lock:
            self._atomic(
                self.root / "rules.json",
                json.dumps([r.to_dict() for r in self.rules.values()], indent=1),
            )
            self._atomic(self.root / "questions.json", json.dumps(self.questions, indent=1))

    @staticmethod
    def _atomic(path: Path, text: str) -> None:
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(text)
        os.replace(tmp, path)

    def _changed(self, r: Rule, old, new, reason: str) -> None:
        if self.on_change is not None:
            self.on_change(r, old, new, reason)

    def _log(self, **rec) -> None:
        with self.lock, open(self.root / "history.jsonl", "a") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **rec}, sort_keys=True) + "\n")

    def history(self, rule_id: str | None = None) -> list[dict]:
        p = self.root / "history.jsonl"
        with self.lock:
            if not p.exists():
                return []
            text = p.read_text()
        out = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        return [h for h in out if rule_id is None or h.get("id") == rule_id]

    # -- writes ------------------------------------------------------------------
    def next_id(self, kind: str) -> str:
        p = PREFIX.get(kind, "x")
        with self.lock:
            n = sum(
                1 for r in self.rules.values() if r.id.startswith(p) and r.id[len(p) :].isdigit()
            )
            while f"{p}{n + 1}" in self.rules:
                n += 1
        return f"{p}{n + 1}"

    def duplicate_of(self, rule: Rule) -> str | None:
        d = rule.digest()
        with self.lock:
            return next((r.id for r in self.rules.values() if r.digest() == d), None)

    def add(
        self, rule: Rule, by: str = "miner", embargo: bool = False, visibility: str | None = None
    ) -> Rule:
        """Add a proposed rule (status ``candidate``): assigns its id and visibility (by
        hash, unless ``visibility`` forces one: a promoted rule's replacement is held).
        ``embargo``: added by a background pass, not usable until ``admit``."""
        if rule.kind not in KINDS:
            rule.kind = "edge"
        with self.lock:
            rule.id = self.next_id(rule.kind)
            rule.status = "candidate"
            rule.visibility = visibility or (
                "held" if held_by_hash(rule, self.held_fraction) else "visible"
            )
            self.rules[rule.id] = rule
            if embargo:
                self.embargo.add(rule.id)
            self._log(
                id=rule.id,
                version=rule.version,
                frm=None,
                to="candidate",
                by=by,
                reason=rule.reason or "proposed",
                visibility=rule.visibility,
                origin=rule.origin,
            )
            self.save()
        self._changed(rule, None, "candidate", rule.reason or "proposed")
        return rule

    def set_status(self, rule_id: str, status: str, reason: str, by: str) -> tuple[str, str]:
        """Returns (old, new) status; every change is recorded in ``history.jsonl``."""
        if status not in STATUSES:
            raise ValueError(f"unknown rule status {status!r}")
        with self.lock:
            r = self.rules[rule_id]
            old = r.status
            r.status, r.reason = status, reason
            self._log(id=rule_id, version=r.version, frm=old, to=status, by=by, reason=reason)
            self.save()
        self._changed(r, old, status, reason)
        return old, status

    def repair(
        self, rule_id: str, code: str, reason: str, by: str = "arbiter", embargo: bool = False
    ) -> Rule:
        """Replace a rule's code (a new version, back to ``candidate`` until revalidated)."""
        with self.lock:
            r = self.rules[rule_id]
            old = r.status
            r.code, r.version, r.status, r.reason = code, r.version + 1, "candidate", reason
            r.checks = {}
            if embargo:
                self.embargo.add(rule_id)
            self._log(
                id=rule_id,
                version=r.version,
                frm=old,
                to="candidate",
                by=by,
                reason="repaired: " + reason,
            )
            self.save()
        self._changed(r, old, "candidate", "repaired: " + reason)
        return r

    def promote(
        self, rule_id: str, reason: str, by: str = "arbiter", embargo: bool = False
    ) -> bool:
        """A held rule that caught a real bug becomes visible, so the fix loop can use it
        (build v3 §2). Recorded in ``history.jsonl`` as a visibility change. Returns False
        if the rule was not held."""
        with self.lock:
            r = self.rules[rule_id]
            if r.visibility != "held":
                return False
            r.visibility = "visible"
            if embargo:
                self.embargo.add(rule_id)
            self._log(
                id=rule_id,
                version=r.version,
                by=by,
                frm_visibility="held",
                to_visibility="visible",
                reason="promoted: " + reason,
            )
            self.save()
        return True

    def admit(self, rule_ids) -> list[str]:
        """End the embargo on ``rule_ids``: from now on gates, scores and workspaces use
        them. Returns the ids that were embargoed."""
        with self.lock:
            out = sorted(set(rule_ids) & self.embargo)
            self.embargo -= set(out)
        return out

    def record_verdict(self, rule_id: str, verdict: dict) -> None:
        with self.lock:
            r = self.rules[rule_id]
            r.verdicts.append({**verdict, "version": r.version})
            if verdict.get("verdict") == "code_wrong":
                r.caught += 1
            self._log(
                id=rule_id,
                version=r.version,
                verdict=verdict.get("verdict"),
                by="arbiter",
                reason=verdict.get("reason", ""),
            )
            self.save()

    def mark_fixed(self, rule_ids) -> list[str]:
        """Rules that caught a code bug and now pass on a kept candidate."""
        out = []
        with self.lock:
            for rid in rule_ids:
                r = self.rules.get(rid)
                if r is not None and r.caught and not r.fixed:
                    r.fixed = True
                    out.append(rid)
                    self._log(id=rid, version=r.version, by="score", fixed=True)
            if out:
                self.save()
        return out

    def add_question(self, rule_id: str, text: str) -> None:
        with self.lock:
            self.questions.append({"rule": rule_id, "text": text, "ts": round(time.time(), 3)})
            self.save()

    # -- reads ---------------------------------------------------------------------
    def select(
        self,
        status: str | tuple = "active",
        visibility: str | None = None,
        due: set[str] | None = None,
        admitted: bool = False,
    ) -> list[Rule]:
        """Rules by status and visibility. ``due``: the milestones built so far; a rule is
        due when every milestone it touches is (rules without milestones always are).
        ``admitted``: skip embargoed rules (what the build itself may use)."""
        st = (status,) if isinstance(status, str) else tuple(status)
        with self.lock:
            return [
                r
                for r in self.rules.values()
                if r.status in st
                and (visibility is None or r.visibility == visibility)
                and (due is None or set(r.milestones) <= set(due))
                and not (admitted and r.id in self.embargo)
            ]

    def active(
        self, visibility: str | None = None, due: set[str] | None = None, admitted: bool = False
    ) -> list[Rule]:
        return self.select("active", visibility, due, admitted)

    def key(self, rules: list[Rule]) -> str:
        """Identity of a rule set (ids and versions): score records carry it, so a record
        scored before the set grew is stale."""
        return hashlib.sha256(
            json.dumps(sorted((r.id, r.version, r.digest()) for r in rules)).encode()
        ).hexdigest()[:16]

    def export(
        self, dest: Path, visibility: str | None = "visible", rules: list[Rule] | None = None
    ) -> list[str]:
        """Write rule files to ``dest`` (default: the visible, admitted active rules). For
        a workspace pass nothing but ``visibility="visible"``; held rules never go there."""
        rules = self.active(visibility, admitted=True) if rules is None else rules
        if visibility == "visible" and any(r.visibility != "visible" for r in rules):
            raise ValueError("held rules may not be exported to a workspace")
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        out = []
        for r in rules:
            (dest / r.test_file).write_text(r.code.rstrip() + "\n")
            out.append(r.test_file)
        return out

    def summary(self) -> dict:
        by = {s: 0 for s in STATUSES}
        with self.lock:
            rules = list(self.rules.values())
        for r in rules:
            by[r.status] += 1
        return {
            **by,
            "held": sum(1 for r in rules if r.status == "active" and r.visibility == "held"),
            "caught": sum(1 for r in rules if r.caught),
        }


def store_root(cache_dir: Path, task_id: str, round_id: int | str) -> Path:
    """Per build: ``<den cache>/rules/<task>/r<round>``."""
    return Path(cache_dir) / "rules" / str(task_id) / f"r{round_id}"
