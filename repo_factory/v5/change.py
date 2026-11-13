"""A revise round: the owner's change request on a held or ready v5 build (AR-PLAN.md §2).

Its own round ``r<N+1>`` of kind ``revise``, so the gate, the loop's ``builds.outcome``, the
build page and the pipeline ledger, which all read the latest round, work unchanged, and the
finished round's record stays as it was. Scope, claims, oracle and the readiness
review are carried from round N (``seed_round``: its state, readiness, findings, claims,
scope and rule store); they do not run again.

Steps (``STAGES``, v5's names, so the ledger's cells are the same), the state saved after
each (``--resume`` continues at the step after the last one done; a session that already ran
is never paid twice):

1. **build**: ``ws`` is a clone of ``out`` at ``repo-factory/<id>`` (what an earlier version of
   repo-factory wrote into it leaves in a commit of its own, EX-PLAN §8.1); in code mode the *baseline* runs
   the product's own tests there (``exam.run``, sandboxed, network off; no model). One session gets the owner's instructions
   (a document), the hold reasons and, in code mode, the own tests failing at baseline. It
   writes ``.repo-factory/change.json``. Code mode with a tests hold and nothing failing now skips
   the session: deliver measures again.
2. **verify** (no model): the scope (nothing under ``.github/``, ``LICENSE``, ``NOTICE``;
   docs mode changes Markdown only), then code mode runs the suite again (nothing that passed
   may fail; a tests hold needs the own suite green) and docs mode scans the changed Markdown
   with the provenance scan (``jargon.py``).
3. **repair** + **reverify**: one resumed session with the findings, then verify again. Still
   failing: ``StageFailed``, the round ends ``error``, and ``out/`` is untouched (only deliver
   writes the branch).
4. **deliver** (no model): commit in ``ws``, then v5's own ``do_deliver`` (the exam's record
   of the delivered tree, ``tests_green``, the readiness scorecard and the evidence, the
   README's commands run as written), as a new commit on ``repo-factory/<id>`` whose parent
   is the old head (``add_commit``). A docs round whose non-documentation files are unchanged
   reuses the revised round's exam record instead (``exam.reuse``; only the README's test
   commands run again, if they changed). Then ``revise_outcome`` and ``build_finished``."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

import yaml

from .. import gitcmd
from ..costs import ledger
from ..resources import POLICIES
from . import exam
from .common import Budget, prompt
from .pipeline import Run, StageFailed, load_rules

STAGES = ["build", "verify", "repair", "reverify", "deliver"]  # v5's names: ledger unchanged
CARRIED = ("scope", "claims", "oracle", "readiness")
MODES = ("auto", "docs", "code")
POLICY = POLICIES / "v5-change"
BASE_POLICY = POLICIES / "v5"
ACC = "tests/acceptance"
NEVER = (".github/", "LICENSE", "NOTICE")
# carried with the round: what deliver and the gate read
SEED = ("state.json", "readiness.json", "findings.json", "claims.json", "scope.json", "SCOPE.md")
MODELS_ENV = "REPO_FACTORY_REVISE"  # the loop's revise.models and revise.max_usd, as JSON
DOCS_HOLDS = ("honesty", "jargon")


class ReviseError(RuntimeError):
    pass


def _rounds(den_root: Path) -> list[int]:
    v5 = Path(den_root) / "v5"
    try:
        return sorted(
            int(p.name[1:]) for p in v5.iterdir() if p.is_dir() and re.fullmatch(r"r\d+", p.name)
        )
    except OSError:
        return []


def _state(den_root: Path, rnd: int) -> dict | None:
    try:
        return json.loads((Path(den_root) / "v5" / f"r{rnd}" / "state.json").read_text())
    except (OSError, ValueError):
        return None


def base_round(den_root: Path) -> int | None:
    """The latest round with ``deliver`` in its saved ``done``: what a revise starts from."""
    for r in reversed(_rounds(den_root)):
        st = _state(den_root, r)
        if st and "deliver" in (st.get("done") or []):
            return r
    return None


def seed_round(den, task_id: str, prev: int, rid: int) -> dict:
    """Round ``rid`` starts as round ``prev`` ended: its record files and its rule store are
    copied (the finished round stays as it was); the copied state has no step done yet.
    Returns that state."""
    from ..rules.store import store_root

    src = Path(den.root) / "v5" / f"r{prev}"
    dst = Path(den.root) / "v5" / f"r{rid}"
    dst.mkdir(parents=True, exist_ok=True)
    for name in SEED:
        if (src / name).is_file():
            shutil.copy2(src / name, dst / name)
    a, b = store_root(den.cache, task_id, prev), store_root(den.cache, task_id, rid)
    if Path(a).is_dir() and not Path(b).exists():
        shutil.copytree(a, b)
    st = json.loads((dst / "state.json").read_text())
    st.update(
        done=[],
        failed=None,
        stop=None,
        partial=None,
        cut_off=False,
        carried=None,
        build_failures=0,
        wall_s=0.0,
    )
    (dst / "state.json").write_text(json.dumps(st, default=str))
    return st


def pick_mode(gate: dict | None, publish: dict | None = None) -> str:
    """``docs`` when only honesty or jargon holds it (the gate passed; the pre-push wording
    checks held it); else ``code``. ``publish``: the idea's publish record, or its reason."""
    g = gate or {}
    failing = [k for k, c in (g.get("checks") or {}).items() if not c.get("ok")]
    if failing or (g.get("verdict") and g.get("verdict") != "ready"):
        return "code"
    rec = publish if isinstance(publish, dict) else {}
    reason = publish if isinstance(publish, str) else ""
    holds = set()
    if rec:
        if (rec.get("honesty") or {}).get("ok") is False:
            holds.add("honesty")
        if rec.get("jargon"):
            holds.add("jargon")
        if rec.get("secrets"):
            holds.add("secrets")
    for k, v in (
        ("honesty check", "honesty"),
        ("jargon scan", "jargon"),
        ("secrets scan", "secrets"),
    ):
        if k in reason:
            holds.add(v)
    return "docs" if holds and holds <= set(DOCS_HOLDS) else "code"


def git(*args: str, cwd: Path, check: bool = True) -> str:
    p = gitcmd.run(args, cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise ReviseError(
            f"git {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:300]}"
        )
    return p.stdout


def commit(root: Path, msg: str) -> None:
    who = (
        []
        if git("config", "user.email", cwd=root, check=False).strip()
        else ["-c", "user.name=repo-factory", "-c", "user.email=repo-factory@localhost"]
    )
    git(*who, "commit", "-q", "--allow-empty", "-m", msg, cwd=root)


def add_commit(cfg, art: Path, out: Path, message: str) -> str:
    """``repo-factory/<id>`` in ``out`` gets the delivered tree as a **new commit** on its head (the
    old head is its parent; ``main`` and the history stay), unlike ``make_branch``, which
    recreates the repo. Returns the new head."""
    from .workspace import _copy_tree, _strip_scaffold

    branch = f"repo-factory/{cfg.id}"
    report = Path(out) / "REPORT.md"
    report.unlink(missing_ok=True)  # rewritten after; never committed
    git("checkout", "-q", branch, cwd=out)
    for rel in git("ls-files", "-z", cwd=out).split("\0"):
        if rel and (Path(out) / rel).is_file():
            (Path(out) / rel).unlink()
    _copy_tree(Path(art), Path(out))
    _strip_scaffold(cfg, Path(out), own_gitignore=True)
    # indexed again, so what the .gitignore names leaves even when an older head tracked it
    git("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".", cwd=out)
    git("add", "-A", cwd=out)
    commit(Path(out), message)
    return git("rev-parse", "HEAD", cwd=out).strip()


def violations(changed: list[str], mode: str) -> list[str]:
    """What a revise may never change: ``.github/``, ``LICENSE``, ``NOTICE``, a file outside
    the tree, a secret-shaped file; in docs mode, anything but Markdown. (repo-factory's exam is not
    in the tree, EX-PLAN §3.4: nothing there to protect.)"""
    from ..maintain.revise import scope_violations

    bad = list(scope_violations(changed))
    for f in changed:
        if any(f == x or f.startswith(x) for x in NEVER) and not any(f in b for b in bad):
            bad.append(f"{f}: a revise never changes this file")
        elif mode == "docs" and not f.lower().endswith(".md"):
            bad.append(f"{f}: docs mode changes Markdown only")
    return list(dict.fromkeys(bad))


def statuses(t: dict) -> dict[str, str]:
    out = dict(t.get("tests") or {})
    for f in t.get("errors") or []:
        out[f] = "error"
    return out


def own(st: dict) -> dict[str, str]:
    return {k: v for k, v in st.items() if not k.split("::")[0].startswith(ACC + "/")}


def counts(st: dict) -> dict:
    o = own(st)
    return {"own_failed": sum(1 for v in o.values() if v in ("failed", "error")), "total": len(o)}


def doc(name: str, text: str) -> str:
    return f'<<<document name="{name}">>>\n{text.strip()}\n<<<end>>>'


class ChangeRun(Run):
    """A v5 ``Run`` loaded from the round it revises (its claims, rule store, readiness
    record, findings), which runs the change steps instead of the build's."""

    STATE = Run.STATE + ("change",)

    def __init__(self, *a, change_rules: dict | None = None, tests=None, **kw):
        super().__init__(*a, fresh=False, **kw)
        self.change: dict = {}
        self.crules = change_rules or {}
        self._tests = tests  # a test double for the suite runs

    # -- plumbing --------------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self.change.get("mode") or "code"

    def caps(self, stage: str) -> dict:
        key = "docs" if stage == "build" and self.mode == "docs" else stage
        caps = dict(self.crules.get(key) or self.crules.get(stage) or {})
        over = _overrides()
        model = (over.get("models") or {}).get("repair" if stage == "repair" else "build")
        if model:
            caps["model"] = model
        return caps

    def suite(self) -> dict:
        """The product's own tests on ``ws``: the exam record's own section
        (``exam.run(own=True)``, sandboxed, network off), cached by tree hash, so a revise
        whose change touches nothing the hash sees costs no second run."""
        if self._tests is not None:
            return self._tests(self.ws)
        from . import exam

        rec = exam.run(self, self.ws, rules=[], own=True)
        out = exam.suite_of(rec)
        if not rec.get("ran"):
            out.update(returncode=-1, why=rec.get("why") or "not run")
        return out

    def changed(self) -> list[str]:
        out = git("diff", "--name-only", "HEAD", cwd=self.ws).split()
        out += git("ls-files", "--others", "--exclude-standard", cwd=self.ws).split()
        return sorted({f for f in out if not f.startswith(".repo-factory/")})

    def note(self) -> dict:
        try:
            d = json.loads((self.ws / ".repo-factory" / "change.json").read_text())
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def holds_text(self) -> str:
        h = self.change.get("holds") or {}
        lines = [f"- {r}" for r in h.get("reasons") or []]
        lines += [f"- {i}" for i in h.get("issues") or []]
        return doc("hold reasons", "\n".join(lines) or "(none recorded)")

    def text(self, section: str | None = None, **vals) -> str:
        return prompt(POLICY, "change", section, **vals)

    def skip(self, name: str, why: str) -> None:
        self.emit(
            "v5_stage",
            stage=name,
            status="skipped",
            usd=0.0,
            wall_s=0.0,
            tokens_in=0,
            tokens_out=0,
            why=why,
        )
        if name not in self.done:
            self.done.append(name)

    # -- the steps -------------------------------------------------------------------------
    def go(self, steps: int | None = None, t0: float | None = None) -> bool:
        t0 = time.time() if t0 is None else t0
        self.failed = None
        plan = [
            ("build", self.do_change),
            ("verify", self.do_check),
            ("repair", self.do_fix),
            ("reverify", self.do_recheck),
            ("deliver", self.do_ship),
        ]
        n = 0
        for name, fn in plan:
            if name in self.done:
                continue
            if steps is not None and n >= steps:
                self.save(time.time() - t0)
                return False
            try:
                self.stage(name, fn)
            except StageFailed as e:
                self.failed = {"stage": e.stage, "reason": e.reason, "kind": e.kind}
                self.stop = f"the {e.stage} stage failed: {e.reason}"
                self.save(time.time() - t0)
                return True
            n += 1
            if name not in self.done:
                self.done.append(name)
            if name == "verify" and not self.change.get("found"):
                for s in ("repair", "reverify"):
                    self.skip(s, "verify found nothing")
            self.save(time.time() - t0)
        return True

    def checkout(self) -> None:
        if (self.ws / ".git").is_dir():
            return
        if self.ws.exists():
            shutil.rmtree(self.ws)
        git("clone", "-q", "-b", self.branch, str(self.out), str(self.ws), cwd=self.root)
        excl = self.ws / ".git" / "info" / "exclude"
        excl.parent.mkdir(parents=True, exist_ok=True)
        with open(excl, "a") as f:
            f.write("\n.repo-factory/\ntranscript.jsonl\nstderr.txt\n.venv/\n")
        # EX-PLAN §8.1: what an earlier version of repo-factory wrote into the delivered tree (its
        # acceptance suite, READINESS.md, ASSUMPTIONS.md, the review SECURITY.md) leaves it in
        # a commit of its own, before the baseline: the round delivers a clean tree
        from . import exam

        gone = exam.strip_legacy(self.ws)
        if gone:
            git("add", "-A", cwd=self.ws)
            commit(
                self.ws,
                "repo-factory's own files move out of the tree: "
                + ", ".join(gone)
                + "\n\nThe checks and the reports ship with each release instead.",
            )
            self.change["legacy_removed"] = gone

    def do_change(self) -> None:
        c = self.change
        self.checkout()
        if "baseline" not in c and self.mode == "code":  # only code mode reads it
            base = self.suite()
            c["baseline"] = statuses(base)
            c["baseline_ran"] = base.get("returncode") != -1
            c["details"] = {k: str(v)[-1500:] for k, v in (base.get("details") or {}).items()}
            c["before"] = counts(c["baseline"])
            self.save()
        failing = sorted(
            k for k, v in own(c.get("baseline") or {}).items() if v in ("failed", "error")
        )
        tests_hold = "tests" in ((c.get("holds") or {}).get("failing") or [])
        if self.mode == "code" and tests_hold and c.get("baseline_ran") and not failing:
            c["skipped_session"] = "nothing fails now"
            return
        if self.mode == "code" and failing:
            det = c.get("details") or {}
            lines = []
            for k in failing[:20]:
                out = next(
                    (
                        v
                        for t, v in det.items()
                        if t.split("::")[0] == k.split("::")[0] and (t == k or "::" not in k)
                    ),
                    "",
                )
                lines.append(f"- `{k}`" + (f"\n\n```\n{out.strip()[-800:]}\n```" if out else ""))
            test = self.text("test", failures="\n".join(lines))
        elif self.mode == "docs":
            test = self.text("docs")
        else:
            test = ""
        from .pipeline import product_name

        body = self.text(
            product=product_name(self.ws, self.scope_md(), self.task.id),
            instructions=doc("the owner's request", c.get("instructions") or ""),
            holds=self.holds_text(),
            test=test,
        )
        res, sess = self.session(
            "builder", body, self.ws, "build", self.caps("build"), "change", resume=None
        )
        c["session"] = sess
        c["note"] = self.note()
        if not self.changed():
            raise StageFailed(
                "build",
                "the change session changed nothing ("
                f"{getattr(res, 'exit_reason', None) or 'no reason'})",
            )

    def check(self) -> list[str]:
        c = self.change
        found = [f"out of scope: {b}" for b in violations(self.changed(), self.mode)]
        if self.mode == "code":
            res = self.suite()
            now = statuses(res)
            if res.get("returncode") == -1:
                found.append(f"the suite did not run: {res.get('why') or 'no result'}")
            for k, v in sorted((c.get("baseline") or {}).items()):
                if (
                    v == "passed"
                    and now.get(k) != "passed"
                    and not k.split("::")[0].startswith(ACC + "/")
                ):
                    found.append(
                        f"regression: `{k}` passed before and is {now.get(k) or 'gone'} now"
                    )
            if "tests" in ((c.get("holds") or {}).get("failing") or []):
                bad = sorted(k for k, v in own(now).items() if v in ("failed", "error"))
                found += [f"still failing: `{k}`" for k in bad[:20]]
            c["checked"] = counts(now)
        else:
            md = [
                self.ws / f
                for f in self.changed()
                if f.lower().endswith(".md") and (self.ws / f).is_file()
            ]
            from . import jargon

            hits = jargon.scan_run(self, self.ws, md) if md else []
            found += [f"build-process words in {h['file']}:{h['line']} ({h['kind']})" for h in hits]
        c["found"] = found
        self.emit(
            "findings",
            stage="verify",
            confirmed=len(found),
            scenarios=len(found),
            failing=len(found),
        )
        return found

    def do_check(self) -> None:
        if self.change.get("skipped_session"):
            self.change["found"] = []
            return
        self.check()

    def do_fix(self) -> None:
        c = self.change
        text = self.text("repair", findings="\n".join(f"- {f}" for f in c.get("found") or []))
        _, sess = self.session(
            "builder",
            text,
            self.ws,
            "repair",
            self.caps("repair"),
            "change-repair",
            resume=c.get("session"),
        )
        c["repair_session"] = sess
        c["note"] = self.note() or c.get("note")

    def do_recheck(self) -> None:
        found = self.check()
        if found:
            raise StageFailed(
                "reverify",
                "the change still fails its checks: "
                + "; ".join(found)[:600]
                + "; the build is as it was",
            )

    def do_ship(self) -> None:
        c = self.change
        what = " ".join(str((c.get("note") or {}).get("what") or "").split())[:300]
        shutil.rmtree(self.ws / ".repo-factory", ignore_errors=True)  # the session's note: kept
        if self.changed():  # in the state, not shipped
            git("add", "-A", cwd=self.ws)
            commit(self.ws, f"revise round {self.rid}: {what or 'changes asked for'}")
        self.do_deliver()
        changed = git(
            "diff", "--name-only", f"{c.get('head') or 'HEAD~1'}", "HEAD", cwd=self.out, check=False
        ).split()
        self.emit(
            "revise_outcome",
            round=self.rid,
            revises=c.get("revises"),
            mode=self.mode,
            origin=c.get("origin"),
            changed=changed[:100],
            before=c.get("before"),
            after=c.get("after"),
            what=what or None,
            **({"exam_reused": c["exam_reused"]} if c.get("exam_reused") else {}),
        )

    # -- v5's delivery, as a new commit ----------------------------------------------------
    def parent_exam(self) -> tuple[int, dict] | None:
        """A docs round may reuse the delivered record of the round it revises (its
        evidence's ``results.json``): ``exam.reuse`` checks that only documentation
        changed. A code round measures again."""
        prev = self.change.get("revises")
        if self.mode != "docs" or prev is None:
            return None
        p = self.root.parent / f"r{prev}" / exam.DIR / "evidence" / "results.json"
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        return (int(prev), rec) if isinstance(rec, dict) else None

    def delivered_suite(self, art: Path) -> dict:
        s = super().delivered_suite(art)
        if s.get("reused"):
            self._stage_why["deliver"] = s["reused"]
            self.change["exam_reused"] = (self._final.get("reused") or {}).get("round")
        o = s.get("own") or {}
        self.change["after"] = {
            "own_failed": len(o.get("failed") or []),
            "total": o.get("total") or 0,
            "ran": bool(s.get("ran")),
        }
        return s

    def publish_branch(self, art: Path) -> None:
        self.change["head"] = git("rev-parse", self.branch, cwd=self.out).strip()
        what = " ".join(str((self.change.get("note") or {}).get("what") or "").split())[:200]
        add_commit(
            self.cfg,
            art,
            self.out,
            f"repo-factory: revise {self.cfg.id} (round {self.rid})"
            + (f"\n\n{what}" if what else ""),
        )


def _overrides() -> dict:
    try:
        v = json.loads(os.environ.get(MODELS_ENV) or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def revise(
    taskdir: Path,
    note: str,
    *,
    mode: str = "auto",
    origin: str = "cli",
    den: Path | None = None,
    resume: int | None = None,
    harness=None,
    policy: Path = POLICY,
    base_policy: Path = BASE_POLICY,
    subscribers=None,
    holds: dict | None = None,
    tests=None,
    steps: int | None = None,
) -> dict:
    """One revise round of the task's latest delivered v5 round (``resume``: continue round
    ``resume``). ``note``: the owner's instructions (at most 4000 characters); ``mode``
    auto|docs|code; ``holds``: ``{"reasons", "issues", "failing"}`` (the loop's hold, else
    the gate's reasons on the round it revises); ``harness``/``tests``: test doubles."""
    from ..den.store import Den
    from ..events import EventLog, log_path
    from ..loop import gate as gate_mod
    from ..tasks import load_task
    from .workspace import lifecycle

    taskdir = Path(taskdir).resolve()
    task, cfg = load_task(taskdir)
    rules = load_rules(Path(base_policy))
    crules = yaml.safe_load((Path(policy) / "rules.yaml").read_text()) or {}
    d = Den(den or taskdir / ".den")
    d.add_task(task)
    if resume is not None:
        rid = int(resume)
        saved = _state(d.root, rid)
        if not saved or not saved.get("change"):
            raise ReviseError(f"round {rid} is not a revise round with a saved state")
        if "deliver" in (saved.get("done") or []):
            raise ReviseError(f"round {rid} is already delivered")
    else:
        if mode not in MODES:
            raise ReviseError(f"mode is one of {', '.join(MODES)}")
        note = (note or "").strip()[:4000]
        prev = base_round(d.root)
        if prev is None:
            raise ReviseError(
                "no delivered v5 round to revise (only a held or ready build "
                "that delivered can be revised)"
            )
        g = gate_mod.evaluate(taskdir, build=prev)
        failing = [k for k, c in g["checks"].items() if not c.get("ok")]
        h = dict(holds or {})
        h.setdefault("reasons", g["reasons"])
        h.setdefault("issues", [])
        h["failing"] = sorted(set(h.get("failing") or []) | set(failing))
        if mode == "auto":
            mode = pick_mode(
                g,
                " ".join(h.get("reasons") or [])
                + " "
                + ("honesty check" if h.get("issues") else ""),
            )
        rid = d.new_round(f"v{crules.get('version', '1.0')}-change", [task.id], "revise")
        saved = seed_round(d, task.id, prev, rid)
        saved["change"] = {
            "revises": prev,
            "mode": mode,
            "origin": origin,
            "instructions": note,
            "holds": h,
            "requested": time.time(),
        }
    out = Path(saved["out"])
    log = EventLog(log_path(d.root), task.id, rid, subscribers)
    cap = _overrides().get("max_usd")
    run = ChangeRun(
        taskdir,
        task,
        cfg,
        d,
        rid,
        log,
        rules,
        Path(base_policy),
        Budget(float(cap) if cap else None, d.root, rid),
        "none",
        out,
        None,
        harness,
        None,
        None,
        change_rules=crules,
        tests=tests,
    )
    run.load(saved)
    run.change = dict(saved.get("change") or {})
    prev = run.change["revises"]
    t0 = time.time() - float(saved.get("wall_s") or 0.0)
    with (
        ledger.activate(ledger.ledger_path(d.root), rid, events=log),
        lifecycle(d, log, t0, run.budget.spent),
    ):
        if resume is None:
            c = run.change
            log.emit(
                "build_started",
                spec=str(taskdir / "spec.md"),
                taskdir=str(taskdir),
                title=task.id,
                policy=f"v{rules.get('version', '5.0')}",
                harness=getattr(harness, "name", "claude-code"),
                owner="none",
                out=str(out),
                pid=os.getpid(),
                pipeline="v5",
                cap_usd=cap or None,
                kind="revise",
                revises=prev,
                origin=c["origin"],
                mode=c["mode"],
                instructions=c["instructions"],
                stages=list(STAGES),
                factory_version=exam.factory_version(),
            )
            for s in CARRIED:
                log.emit(
                    "v5_stage",
                    stage=s,
                    status="skipped",
                    usd=0.0,
                    wall_s=0.0,
                    tokens_in=0,
                    tokens_out=0,
                    why=f"carried from round {prev}",
                )
            run.save()
        else:
            log.emit(
                "build_resumed",
                pid=os.getpid(),
                done=list(run.done),
                **({"retries": run.failed["stage"]} if run.failed else {}),
            )
        finished = run.go(steps, t0)
        wall = round(time.time() - t0, 1)
        cost = run.budget.spent()
        totals = dict(cost_usd=round(cost, 4), wall_s=wall, cost_by_stage=run.budget.by_stage())
        if finished and run.failed:
            f = run.failed
            log.emit(
                "build_finished",
                status="error",
                stage=f["stage"],
                failure=f["kind"],
                error=f"revise failed in {f['stage']}: {f['reason']}",
                **totals,
            )
        elif finished:
            log.emit("build_finished", status="ok", **totals)
    return {
        "task": task.id,
        "round": rid,
        "revises": prev,
        "mode": run.mode,
        "finished": finished,
        "failed": run.failed,
        "branch": run.branch,
        "out": str(out),
        "den": str(d.root),
        "cost_usd": round(cost, 4),
        "wall_s": wall,
        "events": str(log.path),
        "done_steps": list(run.done),
        "change": run.change,
    }
