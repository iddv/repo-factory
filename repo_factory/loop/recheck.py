"""Re-check with current repo-factory: a build is judged by the repo-factory version that built
it; this click runs the installed version's checks on the same delivered tree and says where
the two disagree. A re-check that only turns holds into passes becomes the build's verdict
(``clears``: judged by the re-checking version, the build ready when nothing else
holds it); one that finds a new failure, or nothing different, changes no build's state.

``run(idea, cfg)`` exports the delivered branch (what an earlier version of repo-factory wrote into
it leaves first: the installed version would not have delivered it, ``exam.strip_legacy``),
runs the exam on it (``exam.run``: the product's own tests, the README's test commands as
written and every scenario, the flake protocol on) and the no-model pre-push checks (secrets,
the provenance scan), then recomputes the gate's checks that depend on them:
``tests`` (the README's commands), ``completeness`` (each flow's scenarios on this run) and
``security`` (an open critical or high finding whose probe now passes is closed), and
``safe_defaults`` with the README's quick start run again as written (``quickstart``). The
rest of the gate (``documents``, ``delivered``) is the round's record. What differs is measured
against the verdict the build is held under (``judge``), so a hold the installed version no
longer makes clears. No model runs: wording is never a check here.

The record goes to ``<round>/exam/results/<tree>.recheck-<version>.json`` and
``<round>/recheck-<version>.json``, and its summary to the idea's ``gate.recheck``. A request
never waits on it: the press starts ``repo-factory loop recheck ID`` detached (``spawn``)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from .. import proc
from ..errors import LoopError
from . import gate as gate_mod

SPAWN_ENV = "REPO_FACTORY_RECHECK_SPAWN"  # "0": a press never starts one (the tests)


def installed() -> str:
    from ..v5.exam import factory_version

    return factory_version()


def built_by(round_dir: Path) -> str | None:
    """The repo-factory version that built a round: its exam manifest's, else its ``build_started``
    event's; None for a round from before versions were recorded."""
    try:
        v = json.loads((Path(round_dir) / "exam" / "manifest.json").read_text()).get(
            "factory_version"
        )
        if v:
            return str(v)
    except (OSError, ValueError):
        pass
    rnd = Path(round_dir).name[1:]
    log = Path(round_dir).parent.parent / "events.jsonl"
    try:
        for line in log.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if (
                e.get("type") == "build_started"
                and str(e.get("build")) == rnd
                and e.get("factory_version")
            ):
                return str(e["factory_version"])
    except OSError:
        pass
    return None


def _json(p: Path) -> dict:
    try:
        d = json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def recompute(checks: dict, rec: dict, round_dir: Path) -> dict:
    """The gate's checks that the exam record decides, again, on ``rec``; the others as
    recorded. Returns a new dict of checks."""
    out = {k: dict(v) for k, v in (checks or {}).items()}
    own = rec.get("own")
    if own is not None:
        bad = [c for c in own.get("commands") or [] if c.get("verdict") == "red"]
        if not own.get("commands"):
            out["tests"] = {
                "ok": False,
                "detail": "the README documents no test command that can run here",
            }
        else:
            out["tests"] = {
                "ok": not bad,
                "detail": (
                    "the README's test commands run green as written: "
                    + ", ".join(f"`{c['cmd']}`" for c in own["commands"][:4])
                )
                if not bad
                else "a documented test command fails as written: "
                + ", ".join(f"`{c['cmd']}` ({c.get('status')})" for c in bad[:4]),
            }
    scen = rec.get("scenarios") or {}
    comp = _json(Path(round_dir) / "findings.json").get("completeness") or {}
    if comp.get("flows") and scen:
        rows = []
        for f in comp["flows"]:
            ids = [i for i in f.get("scenarios") or [] if i in scen]
            ok = bool(ids) and all(scen[i]["status"] == "passed" for i in ids)
            # a flow the harness could not measure then, and still can't, stays a gap
            was = f.get("status")
            rows.append(
                (
                    f.get("id"),
                    f.get("title"),
                    "works" if ok else was if was in gate_mod.NOT_MEASURED else "broken",
                )
            )
        out["completeness"] = gate_mod.completeness_check(rows)
    sec = _json(Path(round_dir) / "readiness.json").get("security") or {}
    if sec.get("ran") and scen:
        still = []
        for f in sec.get("findings") or []:
            if str(f.get("severity")).lower() not in ("critical", "high"):
                continue
            st = str(f.get("status")).lower()
            rid = f.get("rule")
            if rid in scen:
                st = "passes" if scen[rid]["status"] == "passed" else "open"
            if st not in gate_mod.CLOSED:
                still.append(f"{f.get('severity')}: {f.get('title')}")
        out["security"] = {
            "ok": not still,
            "detail": "no open critical or high finding"
            if not still
            else "open: " + "; ".join(still),
        }
    return out


def compare(was: dict, new: dict) -> list[dict]:
    """The checks whose result differs: ``[{"check", "was", "now", "detail"}]``."""
    return [
        {"check": k, "was": was[k]["ok"], "now": new[k]["ok"], "detail": new[k]["detail"]}
        for k in new
        if k in was and bool(was[k].get("ok")) != bool(new[k]["ok"])
    ]


def clears(g: dict, rec: dict) -> dict | None:
    """AU1 #3: a re-check that turned holds into passes and turned nothing into a failure is
    the verdict: the held gate ``g`` with the re-check's results for its checks and
    ``judged_by`` the re-checking version. The goalposts that moved in the product's favour
    are on the record (``judged_by``), never against it: a re-check that finds a new failure,
    or finds nothing different, changes nothing. None: the build stays as it was."""
    if (rec or {}).get("state") != "done" or (g or {}).get("verdict") != "held":
        return None
    d = rec.get("differs") or []
    if not all(x.get("now") for x in d) or not any(
        x["check"] in (g.get("checks") or {}) for x in d
    ):
        return None
    now = rec.get("checks") or {}
    checks = {k: dict(now.get(k) or v) for k, v in (g.get("checks") or {}).items()}
    reasons = [f"{k}: {v.get('detail')}" for k, v in checks.items() if not v.get("ok")]
    return {
        **g,
        "checks": checks,
        "reasons": reasons,
        "verdict": "held" if reasons else "ready",
        "gaps": gate_mod.gaps_of(checks),
        "judged_by": rec.get("version"),
        "rechecked": rec.get("at"),
    }


def quickstart(fin, tree: Path) -> dict:
    """The README's quick start, as written, by the installed harness: the readiness
    ``commands`` item the gate's ``safe_defaults`` reads (``gate.SAFE_ITEMS``)."""
    from ..v5 import commands, exam, pyinstall

    wheels = pyinstall.install(fin, tree, exam.settings(fin)).get("wheels")
    return commands.items(
        commands.run(tree, fin.venv, ("quick",), wheels=wheels), tests=False, restart=False
    )["commands"]


def judge(idea: dict, rec: dict, quick: dict | None, root: Path, at: Path) -> tuple[dict, dict]:
    """``(was, new)``: the checks the build is held under (the idea's recorded gate; the
    record's checks where it has none), and the installed version's on this run: the record
    judged by the installed gate, the exam's checks again (``recompute``), and the quick start
    measured again (``quick``, in a copy of the round's records at ``at``). A hold that the
    installed version no longer makes, from a fixed harness or gate, differs as a pass (AU1
    #3: Repo Recall's F5, a flow its sandbox could not measure, held it 105 h)."""
    g = idea.get("gate") or {}
    task = Path(idea["task_dir"])
    old = gate_mod.evaluate(task, build=g.get("round"))
    new = recompute(old["checks"], rec, root)
    if quick is not None:
        files = (
            gate_mod.delivered_files(Path(g["out"]), g["branch"])
            if (Path(g["out"]) / ".git").exists()
            else set()
        )
        new["safe_defaults"] = gate_mod.with_item(root, files, quick, at)["safe_defaults"]
    held = {k: v for k, v in (g.get("checks") or {}).items() if isinstance(v, dict)}
    was = {**old["checks"], **held}
    return was, new


def run(idea: dict, cfg) -> dict:
    """The installed version's checks on the idea's delivered tree; the record."""
    from ..v5 import exam, jargon
    from ..v5.rounds import Finished
    from . import secrets
    from .tree import export

    g = idea.get("gate") or {}
    task = Path(idea["task_dir"])
    rnd = g.get("round")
    if not g.get("out") or not g.get("branch") or rnd is None:
        raise LoopError(f"{idea['id']}: nothing was delivered: there is nothing to re-check")
    root = task / ".den" / "v5" / f"r{rnd}"
    version = installed()
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="repo-factory-recheck-") as tmp:
        tree = Path(tmp) / "tree"
        export(Path(g["out"]), g["branch"], tree)
        legacy = exam.strip_legacy(tree)
        fin = Finished.of_task(task, rnd)
        fin.use_tree(tree)
        rec = exam.run(fin, tree, own=True, readme=True, flake=True)
        quick = quickstart(fin, tree)
        prepush = {"secrets": secrets.scan(tree)[:20], "jargon": jargon.scan_round(root, tree)[:20]}
        was, new = judge(idea, rec, quick, root, Path(tmp) / "rd")
    for k in ("secrets", "jargon"):
        new[k] = {
            "ok": not prepush[k],
            "detail": "clean"
            if not prepush[k]
            else "; ".join(f"{h['file']}:{h['line']} {h['kind']}" for h in prepush[k][:6]),
        }
    pre = (idea.get("publish") or {}).get("precheck") or {}
    for k in ("secrets", "jargon"):
        if pre.get("state") == "done":
            was[k] = {"ok": not pre.get(k), "detail": "as recorded at the pre-push checks"}
    differs = compare(was, new)
    out = {
        "version": version,
        "judged_by": built_by(root),
        "at": time.time(),
        "wall_s": round(time.time() - t0, 1),
        "round": rnd,
        "tree": rec.get("tree"),
        "exam": rec.get("key"),
        "legacy_removed": legacy,
        "checks": new,
        "verdict": "held" if any(not c["ok"] for c in new.values() if c is not None) else "ready",
        "differs": differs,
        "same": not differs,
        "state": "done",
    }
    (root / "exam" / "results").mkdir(parents=True, exist_ok=True)
    (root / "exam" / "results" / f"{rec.get('tree')}.recheck-{version}.json").write_text(
        json.dumps(rec, default=str)
    )
    (root / f"recheck-{version}.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def summary(rec: dict | None) -> dict | None:
    """What the build page shows of a re-check."""
    if not rec:
        return None
    return {
        k: rec.get(k)
        for k in (
            "state",
            "version",
            "judged_by",
            "at",
            "same",
            "differs",
            "verdict",
            "pid",
            "started",
            "error",
            "round",
        )
    }


def words(rec: dict | None) -> str | None:
    """ "re-checked with 0.4.1: same" or the differing checks."""
    if not rec or rec.get("state") != "done":
        return None
    if rec.get("same"):
        return f"re-checked with {rec['version']}: same"
    return f"re-checked with {rec['version']}: " + "; ".join(
        f"{d['check']} {'passes' if d['now'] else 'fails'} now" for d in rec.get("differs") or []
    )


def spawn(cfg, idea_id: str) -> int | None:
    """Start ``repo-factory loop recheck ID`` detached (its output in ``publish/<id>.recheck.log``):
    what a press does. Returns its pid."""
    if os.environ.get(SPAWN_ENV) == "0":
        return None
    d = cfg.layout.publish
    d.mkdir(parents=True, exist_ok=True)
    p = proc.spawn_detached(
        [
            sys.executable,
            "-m",
            "repo_factory.cli",
            "loop",
            "--home",
            str(cfg.home),
            "recheck",
            idea_id,
        ],
        log=d / f"{idea_id}.recheck.log",
    )
    return p.pid


def judged_line(g: dict) -> str | None:
    """ "judged by repo-factory 0.3.3" when the installed version differs from the builder's."""
    by, now = g.get("judged_by"), installed()
    if not by or by == now:
        return None
    return f"judged by repo-factory {by}"
