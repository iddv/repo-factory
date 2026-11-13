"""A stand-in for ``repo-factory build <task> --policy .. --owner none --harness .. [--resume R]`` in
the loop's tests: it writes what a v5 build leaves behind (events, ``<den>/v5/r<n>/``
files, a delivery repo with a ``repo-factory/<id>`` branch) and nothing else. No model calls.

``<task>/fake.json`` steers it: ``start_delay`` (seconds before its round begins: a slow
start), ``sleep`` (seconds before finishing), ``worker`` (start a
``sleep`` in a session of its own, its pid in ``<task>/worker.pid``: a build's Claude
session), ``ignore_term`` (SIGTERM does nothing), ``outcome`` (``ok``,
``error`` or ``auth``, a failed build stage on the Claude login; a list is taken one item
per run), ``readiness``/``findings``/``prediction``
overrides, ``files`` (extra delivered files) and ``evidence`` (false: the round has no
evidence folder; EX-PLAN §4.4 puts the reports there, never in the tree). Every run appends its argv to
``<task>/fake_runs.jsonl``."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def emit(log: Path, task: str, build: int, type_: str, **f):
    with open(log, "a") as fh:
        fh.write(json.dumps({"v": 1, "ts": round(time.time(), 3), "task": task, "build": build,
                             "type": type_, **f}) + "\n")


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def main(argv):
    task = Path(argv[0]).resolve()
    resume = int(argv[argv.index("--resume") + 1]) if "--resume" in argv else None
    cfg = json.loads((task / "fake.json").read_text()) if (task / "fake.json").exists() else {}
    # ``repo-factory revise <task> --note-file F --mode M --origin O``: a revise round. fake.json's
    # ``revise`` (outcome ok | error | die: exits without finishing; readiness, files, ...)
    # steers it over the build's settings.
    revise = "--note-file" in argv
    if revise:
        cfg = {**cfg, "outcome": "ok", **(cfg.get("revise") or {})}
    den = task / ".den"
    den.mkdir(exist_ok=True)
    runs = task / "fake_runs.jsonl"
    n_run = len(runs.read_text().splitlines()) if runs.exists() else 0
    with open(runs, "a") as fh:
        fh.write(json.dumps({"argv": argv, "pid": os.getpid()}) + "\n")
    outcome = cfg.get("outcome", "ok")
    if isinstance(outcome, list):
        outcome = outcome[min(n_run, len(outcome) - 1)]
    tid = task.name
    log = den / "events.jsonl"
    time.sleep(float(cfg.get("start_delay", 0)))
    if resume is None:
        prev = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        rnd = 1 + sum(1 for e in prev if e["type"] == "build_started")
        extra = {"kind": "revise", "revises": rnd - 1, "mode": argv[argv.index("--mode") + 1],
                 "origin": argv[argv.index("--origin") + 1],
                 "instructions": Path(argv[argv.index("--note-file") + 1]).read_text()} \
            if revise else {}
        emit(log, tid, rnd, "build_started", spec=str(task / "spec.md"), taskdir=str(task),
             title=tid, policy="v5.0", harness="fake", owner="none", out=str(task / "out"),
             pid=os.getpid(), pipeline="v5", stages=["scope", "build", "deliver"], **extra)
    else:
        rnd = resume
        emit(log, tid, rnd, "build_resumed", pid=os.getpid(), done=["scope"])
    root = den / "v5" / f"r{rnd}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "state.json").write_text(json.dumps({"done": ["scope"], "cap": 14.0,
                                                 "out": str(task / "out")}))
    if outcome == "die":                   # killed before it finished: no build_finished
        (den / "build.pid").unlink(missing_ok=True)
        return 0
    (den / "build.pid").write_text(f"{os.getpid()}\n")
    with open(den / "ledger.jsonl", "a") as fh:
        fh.write(json.dumps({"ts": time.time(), "build": rnd, "caller": "worker",
                             "usd": float(cfg.get("usd", 1.5))}) + "\n")
    if cfg.get("ignore_term"):             # a build that won't stop on SIGTERM: SIGKILL must
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if cfg.get("worker"):                  # a session: its own process group, as Claude Code's
        w = subprocess.Popen(["sleep", "300"], start_new_session=True)
        (task / "worker.pid").write_text(f"{w.pid}\n")
    time.sleep(float(cfg.get("sleep", 0)))
    if outcome == "auth":                  # v5's failed build stage: the Claude login
        emit(log, tid, rnd, "build_finished", status="error", stage="build", failure="auth",
             error="build stage: Claude login expired: run `claude` once on this machine, or "
                   "set up a long-lived token", cost_usd=0.3, wall_s=1.0)
        (den / "build.pid").unlink(missing_ok=True)
        return 1
    if outcome == "error":
        emit(log, tid, rnd, "build_finished", status="error", error=cfg.get("error", "boom"),
             cost_usd=1.5, wall_s=1.0)
        (den / "build.pid").unlink(missing_ok=True)
        return 1
    out = task / "out"
    out.mkdir(exist_ok=True)
    if not (out / ".git").exists():
        git("init", "-q", "-b", "main", cwd=out)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
            "-m", "seed", cwd=out)
    git("checkout", "-q", "-B", f"repo-factory/{tid}", cwd=out)
    # the product's own files (its reporting policy among them); repo-factory's reports go to the
    # round's evidence
    files = {"README.md": "# Clinicbook\n\nBook patients in.\n\n## Install\n\n./run.sh\n",
             "SECURITY.md": "# Security\n\nReport problems privately.\n",
             "SCOPE.md": "# Scope: Clinicbook\n", "REPORT.md": "factory report\n",
             "app.py": "print('hi')\n", **cfg.get("files", {})}
    for name, text in files.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(text)
    git("add", "-A", cwd=out)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m",
        "deliver", cwd=out)
    green = {"status": "green", "why": "ok"}
    ready = {"security": {"ran": True, "findings": cfg.get("findings_sec", [])},
             "checks": {"install": {"commands": ["./run.sh"]}},
             "final": {"items": [{"id": k, "title": k, **green} for k in
                                 ("install", "bind_localhost", "empty_start", "first_admin",
                                  "tests_green")]}}
    ready.update(cfg.get("readiness", {}))
    (root / "readiness.json").write_text(json.dumps(ready))
    (root / "findings.json").write_text(json.dumps(cfg.get("findings", {
        "completeness": {"done": 3, "total": 3, "flows": []}})))
    (root / "prediction.json").write_text(json.dumps(cfg.get("prediction", {"p_hidden": 0.93})))
    if cfg.get("evidence", True):
        ev = root / "exam" / "evidence"
        (ev / "exam" / "_verify" / "scenarios").mkdir(parents=True, exist_ok=True)
        (ev / "exam" / "_verify" / "scenarios" / "test_rule_c1.py").write_text(
            "def test_c1():\n    assert True\n")
        for name, text in (("security.md", "# Security\n"), ("readiness.md", "# Readiness\n"),
                           ("assumptions.md", "# Assumptions\n"), ("README.md", "# Checks\n"),
                           ("results.json", "{}"), ("manifest.json", "{}")):
            (ev / name).write_text(text)
    (root / "state.json").write_text(json.dumps({"done": ["scope", "build", "deliver"]}))
    emit(log, tid, rnd, "delivered", node=3, branch=f"repo-factory/{tid}", out=str(out),
         report=str(out / "REPORT.md"), changed=True)
    emit(log, tid, rnd, "build_finished", status="ok", cost_usd=float(cfg.get("usd", 1.5)),
         wall_s=1.0)
    (den / "build.pid").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
