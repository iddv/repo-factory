"""Runs one exam record's work inside the exam sandbox (``exam.py`` builds the plan and starts
this file under bwrap: ``/work/src`` the tree as delivered, read-only; ``/work/product`` an
empty read-write directory the tree is copied into before every phase; ``/work/exam`` repo-factory's
exam, read-only; ``/work/out`` where the results go). Standard library only; no import of
repo_factory (``cmdrunner.py``, copied beside it, runs the README's commands as an operator).

    python examrunner.py <plan.json>

Phases, each on a fresh copy of the tree at the same path:

1. ``own_pytest``: pytest over the product's tree (its own tests, per test id: what a repair
   round's acceptance compares); ``own_node``: for a tree with an ``npm test`` script, that
   script with Node's junit reporter (per test id too, for a ``node --test`` suite);
2. ``own_cmds``: every test command the README documents, exactly as written;
3. ``scenarios``: the exam's scenarios, with junit output;
4. the flake protocol: a failing scenario or own command is run again, alone, on a fresh
   copy, up to ``reruns`` more times; it stops early once it has failed ``stop_at`` times
   (the verdict can't change any more). A command that timed out is not run again.

Writes ``<out>/result.json``."""

import json
import os
import shutil
import subprocess
import sys
import time


class Driver:
    def __init__(self, plan):
        self.plan = plan
        self.src, self.product = plan["src"], plan["product"]
        self.exam, self.out = plan.get("exam"), plan["out"]
        self.t = plan.get("timeouts") or {}
        self.env = dict(plan.get("env") or {})
        self.n = 0

    # -- the product copy -----------------------------------------------------------------
    def fresh(self, scenarios=False):
        """``/work/product`` as the tree was delivered (the previous phase's leftovers gone);
        the installed dependencies linked in; for the legacy layout (a round from before the
        exam moved out) the exam's ``_verify/`` laid inside, where those scenarios expect
        it."""
        for name in os.listdir(self.product):
            p = os.path.join(self.product, name)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        shutil.copytree(self.src, self.product, symlinks=True, dirs_exist_ok=True)
        for name, path in (self.plan.get("deps") or {}).items():
            dest = os.path.join(self.product, name)
            if not os.path.lexists(dest) and os.path.isdir(path):
                os.symlink(path, dest)
        if scenarios and self.plan.get("layout") == "inside" and self.exam:
            shutil.copytree(
                os.path.join(self.exam, "_verify"),
                os.path.join(self.product, "_verify"),
                dirs_exist_ok=True,
            )

    def stop_all(self):
        """Everything left running in the sandbox (``cmdrunner``'s sweep). Only ever inside
        bwrap's own pid namespace: outside it the sweep would reach the host's processes."""
        if not self.plan.get("sandboxed"):
            return
        from cmdrunner import Runner

        Runner(
            {"repo": self.product, "home": self.env.get("HOME", "/tmp"), "logs": self.out}
        ).stop_all()

    # -- pytest ---------------------------------------------------------------------------
    def pytest(self, targets, junit, rootdir, timeout, pythonpath):
        env = dict(self.env)
        env["PYTHONPATH"] = pythonpath
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--continue-on-collection-errors",
            "--rootdir",
            rootdir,
            f"--junitxml={junit}",
            "-o",
            "junit_family=xunit1",
            *targets,
        ]
        t0 = time.time()
        log = junit + ".log"
        with open(log, "wb") as fh:
            p = subprocess.Popen(
                cmd,
                cwd=self.product,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                rc = p.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, 9)
                p.wait()
                rc, timed_out = -1, True
        self.stop_all()
        return {
            "rc": rc,
            "timeout": timed_out,
            "wall_s": round(time.time() - t0, 1),
            "junit": os.path.basename(junit) if os.path.exists(junit) else None,
            "output": _tail(log),
        }

    def scenario_args(self):
        """(rootdir, PYTHONPATH, how a target is named): the exam at ``/work/exam``, the
        product at ``/work/product``; the legacy layout runs ``_verify/`` inside the copy."""
        if self.plan.get("layout") == "inside":
            return self.product, self.product, lambda f: f
        return (self.exam, f"{self.product}:{self.exam}", lambda f: os.path.join(self.exam, f))

    # -- phases ---------------------------------------------------------------------------
    def own_pytest(self):
        self.fresh()
        return self.pytest(
            [],
            os.path.join(self.out, "own.xml"),
            self.product,
            float(self.t.get("pytest_s", 900)),
            self.product,
        )

    def own_node(self):
        """``npm test`` over the product's tree with Node's junit reporter (a ``node --test``
        suite reports per test, like ``own_pytest``; another runner gives only its exit)."""
        self.fresh()
        junit = os.path.join(self.out, "own-node.xml")
        env = dict(self.env)
        env["NODE_OPTIONS"] = " ".join(
            x
            for x in (
                env.get("NODE_OPTIONS"),
                "--test-reporter=junit",
                f"--test-reporter-destination={junit}",
            )
            if x
        )
        t0 = time.time()
        log = junit + ".log"
        with open(log, "wb") as fh:
            p = subprocess.Popen(
                ["bash", "-c", "npm test"],
                cwd=self.product,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                rc = p.wait(timeout=float(self.t.get("pytest_s", 900)))
                timed_out = False
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, 9)
                p.wait()
                rc, timed_out = -1, True
        self.stop_all()
        return {
            "rc": rc,
            "timeout": timed_out,
            "wall_s": round(time.time() - t0, 1),
            "junit": os.path.basename(junit) if os.path.exists(junit) else None,
            "output": _tail(log),
        }

    def own_cmds(self):
        from cmdrunner import Runner

        out, end = [], time.time() + float(self.t.get("tests_total_s", 1500))
        stop_at, reruns = self.flake()
        for s in self.plan.get("own_cmds") or []:
            runs = []
            while True:
                if time.time() > end:
                    rec = {
                        "cmd": s["cmd"],
                        "where": s.get("where"),
                        "status": "not_run",
                        "why": "the time for the test commands ran out",
                    }
                    break
                self.fresh()
                r = Runner(
                    {
                        "repo": self.product,
                        "home": self.env.get("HOME", "/tmp"),
                        "logs": self.out,
                        "path_prefix": self.plan.get("path_prefix"),
                    }
                )
                r.env.update(self.env)
                left = max(30.0, end - time.time())
                rec = r.step(
                    {"cmd": s["cmd"], "where": s.get("where"), "kind": "test"},
                    serve=False,
                    timeout=min(float(self.t.get("test_s", 900)), left),
                )
                self.stop_all()
                runs.append(rec["status"])
                fails = sum(1 for x in runs if x != "ok")
                if rec["status"] == "ok" and fails == 0:
                    break
                if (
                    rec["status"] == "timeout"
                    or fails >= stop_at
                    or len(runs) > reruns
                    or self.plan.get("flake") is None
                ):
                    break
            rec["runs"] = runs
            out.append(rec)
        return out

    def flake(self):
        f = self.plan.get("flake") or {}
        return max(2, int(f.get("stop_at", 3))), int(f.get("reruns", 0))

    def scenarios(self):
        root, pp, name = self.scenario_args()
        files = self.plan.get("scenarios") or []
        targets = [name(f) for f in (list(reversed(files)) if self.plan.get("reverse") else files)]
        self.fresh(scenarios=True)
        return self.pytest(
            targets,
            os.path.join(self.out, "scenarios.xml"),
            root,
            float(self.t.get("scenarios_s", 600)),
            pp,
        )

    def rerun(self, files):
        """Each file in ``files`` run alone on a fresh copy until the series is decided."""
        root, pp, name = self.scenario_args()
        stop_at, reruns = self.flake()
        out = {}
        for f in files:
            runs = ["failed"]
            while len(runs) <= reruns and sum(1 for x in runs if x != "passed") < stop_at:
                self.fresh(scenarios=True)
                junit = os.path.join(self.out, f"rerun{self.n}.xml")
                self.n += 1
                r = self.pytest([name(f)], junit, root, float(self.t.get("rerun_s", 300)), pp)
                runs.append("passed" if r["rc"] == 0 else "timeout" if r["timeout"] else "failed")
                if r["timeout"]:
                    break
            out[f] = runs
        return out


def failing_files(junit, known):
    """The scenario files (as the plan names them) with a failing, erroring or uncollectable
    test in a junit report."""
    import xml.etree.ElementTree as ET

    out = []
    for tc in ET.parse(junit).getroot().iter("testcase"):
        bad = tc.find("failure") is not None or tc.find("error") is not None
        if not bad:
            continue
        f = tc.get("file") or (tc.get("classname", "").replace(".", "/") + ".py")
        if f not in known:
            f = tc.get("name", "").replace(".", "/") + ".py"  # a collection failure
        if f in known and f not in out:
            out.append(f)
    return out


def _tail(path, n=3000):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def main():
    plan = json.load(open(sys.argv[1]))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    d = Driver(plan)
    res = {"own_pytest": None, "own_node": None, "own_cmds": None, "scenarios": None, "reruns": {}}
    try:
        if plan.get("own_pytest"):
            res["own_pytest"] = d.own_pytest()
        if plan.get("own_node"):
            res["own_node"] = d.own_node()
        if plan.get("own_cmds") and plan.get("sandboxed"):
            res["own_cmds"] = d.own_cmds()
        elif plan.get("own_cmds"):  # README commands start servers: bwrap only
            res["own_cmds"] = [
                {
                    "cmd": s["cmd"],
                    "where": s.get("where"),
                    "status": "not_run",
                    "why": "no bwrap sandbox on this machine",
                    "runs": [],
                }
                for s in plan["own_cmds"]
            ]
        if plan.get("scenarios"):
            res["scenarios"] = d.scenarios()
            with open(os.path.join(plan["out"], "result.json"), "w") as fh:
                json.dump(res, fh)  # the main run is kept even if a rerun dies
            junit = os.path.join(plan["out"], "scenarios.xml")
            if plan.get("flake") and not res["scenarios"]["timeout"] and os.path.exists(junit):
                failed = failing_files(junit, set(plan["scenarios"]))
                if failed:
                    res["reruns"] = d.rerun(failed)
    finally:
        with open(os.path.join(plan["out"], "result.json"), "w") as fh:
            json.dump(res, fh)


if __name__ == "__main__":
    main()
