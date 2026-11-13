"""The lean flow (track LEAN, from EV7 and EV8): Brief → Build → Harden → Check → Fix once →
Publish. Its stage list is ``stages.LEAN``; a policy with ``flow: lean`` (``policies/lean``)
runs it, chosen per build with ``repo-factory build --policy policies/lean`` or the loop's
``build.flow: lean``. Today's flow (``v5``) stays the default.

- **Brief** (``scope``): today's scope step, its prompt unchanged. SCOPE.md stays in the
  round; the build reads it in its prompt.
- **Build**: one Claude Code session on the pitch plus SCOPE.md, with the owner's house rules
  and nothing else of repo-factory's (no "what ships" list: the harden step does that).
- **Harden** (a prompt stage, ``prompts/harden.md``): EV7's harden prompt, adapted:
  robustness, what the security review holds a release for (bounded bodies, connection
  timeouts, caps on what a caller who is not signed in can store), the tests that prove it
  (one end-to-end test per flow of the definition of done), CI, and the docs a user needs,
  every interface the product exposes among them (LEAN3); no invented features or settings.
  It works economically (reads the code once, the whole suite once at the end), with no turn
  cap. It hands repo-factory its
  map of flows to tests in ``.repo-factory/flows.json``, which goes to the round.
- **Check** (code, ``lean_check``): the objective, non-negotiable gates only. The security
  probe (today's prompt; its probes confirm what it finds), then the harness with no model:
  the README's quick start as written, loopback by default (the address the quick start
  listens on), the documented tests green, completeness on the definition of done (each
  flow's tests, by the harden step's map) and the secrets scan.
- **Fix** (code, ``lean_fix``): when a gate failed, **one** builder session on what failed,
  then the harness again; a fix that makes a gate fail that passed is undone. What is still
  red holds at the gate, as today.
- **Publish**: today's delivery, gate, pre-push checks and publish, unchanged. repo-factory's process
  files never ship (``process.py``, every flow).

The records keep their names and shapes (``scope.json``, ``readiness.json``,
``findings.json``'s completeness, the evidence), so the gate, the build page, Re-check and
publish read a lean round as they read the others; ``check.json`` is the lean check's own."""

from __future__ import annotations

import json
from pathlib import Path

from . import exam, process, readiness, repair, verify
from . import scope as scope_mod
from .common import BudgetExceeded, prompt, usd_or_none
from .pipeline import VANILLA_SUFFIX, Run, strip_envs

# rules.yaml's lean sections, with their defaults (policies/lean/rules.yaml says why)
DEFAULTS = {
    "harden": {"max_turns": 120, "max_usd": None, "timeout_s": 3600, "model": None},
    "check": {
        "security": {"max_turns": 60, "max_usd": None, "timeout_s": 1200, "model": None},
        "commands": None,
    },
    "fix": {"max_turns": 120, "max_usd": None, "timeout_s": 3000, "model": None},
}
FLOWS_FILE = "flows.json"  # the harden step's map: flow id -> the tests that walk it
CHECK_FILE = "check.json"  # each measure of the lean check, in order
LOOPBACK = ("127.0.0.1", "::1", "localhost")
HOLDING_ITEMS = ("commands", "bind_localhost", "tests_green")


def _merged(base: dict, over) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merged(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def normalize(rules: dict) -> dict:
    """The lean flow's ``rules.yaml`` with its defaults, plus the sections the shared stage
    library reads (``readiness``: the security probe's caps and the README commands'
    timeouts). ``check.security: false`` runs no probe (the security check is then a gap,
    never a hold, as for any build whose review did not run)."""
    out = dict(rules)
    for k in ("harden", "fix"):
        out[k] = _merged(DEFAULTS[k], rules.get(k))
    check = dict(rules.get("check") or {})
    sec = check.get("security", DEFAULTS["check"]["security"])
    check["security"] = False if sec is False else _merged(DEFAULTS["check"]["security"], sec)
    out["check"] = check
    out["readiness"] = {
        "security": dict(check["security"] or {}),
        "medium": "never",
        **({"commands": dict(check["commands"])} if check.get("commands") else {}),
    }
    return out


def bind_item(res: dict) -> dict | None:
    """``bind_localhost`` from the README's quick start as the harness ran it: the
    addresses each command that started serving listens on (``cmdrunner``'s ``addrs``).
    Green when all are loopback, red when one is not; None when nothing served (a CLI) or
    the run has no addresses."""
    addrs = set()
    served = False
    for r in res.get("quick") or []:
        if r.get("status") == "serving" or r.get("left_serving"):
            served = True
            for a in (r.get("addrs") or {}).values():
                addrs.update(a)
    if not served or not addrs:
        return None
    wide = sorted(a for a in addrs if a not in LOOPBACK and not a.startswith("127."))
    if wide:
        return readiness._item(
            "bind_localhost",
            "red",
            "the quick start listens on " + ", ".join(wide) + " by default, beyond this machine",
            "listen on 127.0.0.1 unless the user configures otherwise",
        )
    return readiness._item(
        "bind_localhost",
        "green",
        "the quick start listens on " + ", ".join(sorted(addrs)) + " only",
    )


def tests_of(flow: list, tests: dict) -> list[str]:
    """The test ids of ``tests`` a flow's entries name. The harness records ``file::name``
    (a test class's name is not kept: ``harness.venv.parse_junit``), so an entry is read as:
    a file (every test in it), ``file::test_name`` or ``file::Class::test_name`` (that test,
    any parameters), ``file::Class`` (the file's tests: the class is not recorded), or a
    bare test name. A Node test's name is free text (``test/e2e.test.js::F1: install and
    first run``): it matches exactly; only a name no test of the file has is a class."""
    out = []
    for e in flow or []:
        e = str(e).strip()
        e = e[2:] if e.startswith("./") else e
        if not e:
            continue
        parts = [p for p in e.split("::") if p]
        file, last = parts[0], parts[-1]
        exact = [
            t
            for t in tests
            if t.partition("::")[0] == file
            and t.partition("::")[2].split("[", 1)[0].split("::")[-1] == last
        ]
        if len(parts) > 1 and (exact or last.startswith("test")):
            out += exact
            continue
        for tid in tests:
            tfile, _, tname = tid.partition("::")
            base = tname.split("[", 1)[0].split("::")[-1]
            if len(parts) == 1:
                hit = tfile == file or base == file
            else:
                hit = tfile == file
            if hit:
                out.append(tid)
    return list(dict.fromkeys(out))


def flow_statuses(dod: list[dict], mapping: dict, tests: dict) -> dict[str, dict]:
    """Each flow of the definition of done: ``works`` (its tests ran and pass), ``broken``
    (one fails), ``unchecked`` (no test of the map ran)."""
    out = {}
    for f in dod:
        ids = tests_of(mapping.get(f["id"]) or mapping.get(str(f["id"]).lower()) or [], tests)
        st = [tests[t] for t in ids if tests[t] != "skipped"]
        out[f["id"]] = {
            "status": "unchecked"
            if not st
            else "works"
            if all(s == "passed" for s in st)
            else "broken",
            "tests": ids,
        }
    return out


class LeanRun(Run):
    """One build of the lean flow: ``Run`` with the lean stage list (``stages.LEAN``) and
    its check's record in ``lean`` (saved with the run)."""

    STATE = Run.STATE + ("lean",)
    FLOW = "lean"
    SCOPE_IN_WORKSPACE = False  # in the build prompt only: it never sits in the tree

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.lean: dict = {"measures": [], "holds": [], "flows": {}}

    def has_ready(self) -> bool:
        return True

    def own_prompt(self, name: str) -> str:
        """The lean policy's own ``prompts/<name>.md``, or "" (a policy without it)."""
        if not (Path(self.policy) / "prompts" / f"{name}.md").is_file():
            return ""
        return prompt(self.policy, name)

    def scope_rules(self) -> str:
        """The lean brief's two rules (EV8): the pitch's own nouns are the definition of
        done; no settings, roles or workflows the pitch didn't ask for."""
        return self.own_prompt("scope_rules")

    def build_text(self, md: str | None) -> str:
        """The pitch plus SCOPE.md, with the owner's house rules; not the list of what a
        product ships (that is the harden step's). A scoped build also gets its stopping
        rule (``prompts/build_rules.md``): done when the brief's definition of done holds
        (LEAN: 77-92 turns on a broad brief). No turn cap: the rule is where to stop, not a
        limit that cuts work off."""
        text = scope_mod.build_prompt(
            self.idea, md, VANILLA_SUFFIX, self.policy, product_rules=False
        )
        rules = self.own_prompt("build_rules").strip() if md else ""
        if not rules:
            return text
        at = text.rfind(VANILLA_SUFFIX)
        return text[:at] + rules + "\n\n" + text[at:] if at >= 0 else text + "\n" + rules

    # -- Check ------------------------------------------------------------------------------
    def lean_check(self) -> None:
        """The security probe, then the harness's measure (``measure``)."""
        sec = self.cfg_of("check").get("security")
        rd = self.ready
        if sec is False:
            rd["security"] = {"ran": False, "why": "the security probe is off in this policy"}
        else:
            product, ex = readiness.prepare(self, "security", "security", stage="check")
            text = prompt(self.policy, "security", spec=self.spec.strip())
            try:
                self.session(
                    "security",
                    text,
                    product,
                    "check",
                    readiness.cfg(self, "security"),
                    "security",
                    caller="security",
                    exam_mount=(ex, False),
                )
            except BudgetExceeded as e:
                rd["security"] = {"ran": False, "why": str(e)}
            else:
                readiness.ingest_security(self, ex)
        self.measure("check")

    def mapping(self) -> dict:
        try:
            m = json.loads((self.root / FLOWS_FILE).read_text())
        except (OSError, ValueError):
            return {}
        if isinstance(m, dict) and isinstance(m.get("flows"), dict):
            m = m["flows"]
        return (
            {str(k).upper(): (v if isinstance(v, list) else [v]) for k, v in (m or {}).items()}
            if isinstance(m, dict)
            else {}
        )

    def measure(self, by: str) -> list[dict]:
        """The harness on the build's head, no model: the deterministic items, the README's
        commands (quick start, restart, tests), the bound address, the security probes, the
        flows' tests, and the scans of the tree as it will ship. Returns what holds."""
        rd = self.ready
        rd["checks"] = {"ran": False, "items": readiness.deterministic(self.ws, self)}
        readiness.commands_step(self)
        bi = bind_item(readiness.run_commands(self, self.ws))
        if bi is not None:
            rd["checks"]["items"]["bind_localhost"] = bi
        # the security probes on this head: a finding whose probe now passes is fixed
        probes = [r for r in self.store.active()]
        if probes:
            for i, v in self.runner(self.ws)(probes).items():
                self.status[i] = v.get("status")
            for f in (rd.get("security") or {}).get("findings") or []:
                rid = f.get("rule")
                if not rid:
                    continue
                ok = self.status.get(rid) == "passed"
                if ok and f.get("status") in ("open", "addressed"):
                    f["status"] = "fixed"
                elif not ok and f.get("status") in ("fixed", "unconfirmed", "passes"):
                    f["status"] = "open"
        # completeness: each flow's own tests, as the harden step mapped them
        dod = (self.scope or {}).get("dod") or []
        tests = {}
        if dod:
            rec = exam.run(self, self.ws, rules=[], own=True)
            tests = rec.get("tests") or {}
        self.lean["flows"] = flow_statuses(dod, self.mapping(), tests)
        # the scans, on the tree as it will ship (repo-factory's process files out)
        tree = verify.copy_repo(self.ws, self.root / "check-tree")
        process.strip(tree, self.root / "check-process")
        from ..loop import secrets

        sec_hits = secrets.scan(tree)
        holds = self.holding(sec_hits)
        self.lean["holds"] = holds
        self.lean["measures"].append(
            {
                "by": by,
                "head": repair.head(self.ws),
                "holds": holds,
                "flows": self.lean["flows"],
                "secrets": sec_hits[:10],
            }
        )
        (self.root / CHECK_FILE).write_text(json.dumps(self.lean, indent=1, default=str))
        readiness.save(self)
        self.write_findings()
        comp = self.completeness()
        self.emit(
            "lean_check",
            by=by,
            holds=[h["id"] for h in holds],
            **({"completeness": [comp["done"], comp["total"]]} if comp else {}),
        )
        return holds

    def holding(self, sec_hits: list) -> list[dict]:
        """What would hold the build at the gate (or stop its push), as a fix brief's items:
        an open critical or high security finding, a red quick start, bound address or test
        command, a broken flow or a secret."""
        out = []
        for f in (self.ready.get("security") or {}).get("findings") or []:
            if f.get("severity") in ("critical", "high") and f.get("status") in (
                "open",
                "addressed",
            ):
                out.append(
                    {
                        "id": f"security:{f.get('id')}",
                        "kind": "security",
                        "title": f.get("title") or "a security finding",
                        "detail": "; ".join(
                            str(x)
                            for x in (
                                f"severity {f.get('severity')}",
                                f.get("where"),
                                f"repro: {f.get('repro')}" if f.get("repro") else None,
                                f"observed: {f.get('observed')}" if f.get("observed") else None,
                                f"expected: {f.get('expected')}" if f.get("expected") else None,
                            )
                            if x
                        ),
                    }
                )
        items = (self.ready.get("checks") or {}).get("items") or {}
        for k in HOLDING_ITEMS:
            it = items.get(k) or {}
            if it.get("status") == "red":
                out.append(
                    {
                        "id": f"check:{k}",
                        "kind": "readiness",
                        "title": it.get("title"),
                        "detail": it.get("why") or "",
                    }
                )
        dod = {f["id"]: f for f in (self.scope or {}).get("dod") or []}
        for fid, st in (self.lean.get("flows") or {}).items():
            if st["status"] == "broken":
                f = dod.get(fid) or {}
                out.append(
                    {
                        "id": f"flow:{fid}",
                        "kind": "flow",
                        "title": f"{fid} {f.get('title') or ''}".strip(),
                        "detail": "these tests of it fail: "
                        + ", ".join(st["tests"][:8])
                        + (f". Done when: {f.get('done')}" if f.get("done") else ""),
                    }
                )
        for kind, hits in (("secrets", sec_hits),):
            for h in hits[:10]:
                out.append(
                    {
                        "id": f"{kind}:{h.get('file')}:{h.get('line')}",
                        "kind": kind,
                        "title": f"{h.get('kind')} in {h.get('file')}",
                        "detail": f"{h.get('file')} line {h.get('line')}: {h.get('kind')}",
                    }
                )
        return out

    def completeness(self) -> dict | None:
        """The definition of done's flows by their own tests (``measure``): ``works``,
        ``broken``, or ``unchecked`` (a gap, never a hold)."""
        dod = (self.scope or {}).get("dod") or []
        if not dod:
            return None
        st = self.lean.get("flows") or {}
        rows = [
            {
                "id": f["id"],
                "title": f["title"],
                "status": (st.get(f["id"]) or {}).get("status") or "unchecked",
                "scenarios": [],
                "tests": (st.get(f["id"]) or {}).get("tests") or [],
            }
            for f in dod
        ]
        return {
            "done": sum(1 for x in rows if x["status"] == "works"),
            "total": len(rows),
            "flows": rows,
        }

    # -- Fix, once ---------------------------------------------------------------------------
    def lean_fix(self) -> None:
        """One builder session on what holds, then the harness again. A fix after which a
        gate fails that passed before is undone (the tree goes back to before the fix)."""
        items = list(self.lean.get("holds") or [])
        if not items:
            self._stage_why["fix"] = "nothing to fix: every check passed"
            return
        fc = self.cfg_of("fix")
        self.budget.window("fix", usd_or_none(fc.get("max_usd"), "fix.max_usd"))
        base = repair.ensure_repo(self.ws, "v5: before the fix")
        md = "\n\n".join(
            f"### {i}. {d['title']}\n{d['detail']}".rstrip() for i, d in enumerate(items, 1)
        )
        text = prompt(self.policy, "fix", spec=self.spec.strip(), findings=md)
        self.emit("fix_round", round=1, status="fixing", sent=len(items))
        try:
            res, _ = self.session("builder", text, self.ws, "fix", fc, "fix1")
        finally:
            strip_envs(self.ws)
            process.collect(self.ws, self.root)
        repair.ensure_repo(self.ws, "v5: the fix")
        before = {d["id"] for d in items}
        after = {d["id"] for d in self.measure("fix")}
        new = sorted(after - before)
        accepted = not new
        if not accepted:
            repair.reset(self.ws, base)
            after = {d["id"] for d in self.measure("fix-undone")}
        why = (
            (
                f"fixed {len(before - after)} of {len(before)}; still failing: "
                + (", ".join(sorted(after)) or "nothing")
            )
            if accepted
            else f"undone: it made {', '.join(new)} fail"
        )
        self.lean["fix"] = {
            "sent": sorted(before),
            "open": sorted(after),
            "accepted": accepted,
            "exit": res.exit_reason,
            "why": why,
        }
        (self.root / CHECK_FILE).write_text(json.dumps(self.lean, indent=1, default=str))
        self._stage_why["fix"] = why
        self.emit(
            "fix_round",
            round=1,
            status="done",
            sent=len(items),
            fixed=len(before - after) if accepted else 0,
            accepted=accepted,
            why=why,
        )
        self.stop = f"fix once: {why}"
