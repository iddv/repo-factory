"""Starting, watching and stopping the loop's builds.

A build is ``repo-factory build <task> --policy policies/v5 --owner none --harness claude-code``
(``build.command`` replaces the ``repo-factory build`` part, for tests), started detached in its own
session, its output appended to ``<task>/.den/build.log`` (as ``repo-factory ui`` does). Detached on
purpose: the daemon can restart, crash or be upgraded while a build keeps going, and the
next daemon adopts it by pid. A build whose process died without finishing is resumed
(``repo-factory build --resume <round>``, v5 saves its state after every step), never restarted
from scratch."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .. import proc
from ..events import load_state
from ..models import build_env
from ..settings.schema import NOT_A_CAP

BRB = [sys.executable, "-m", "repo_factory.cli"]


def command(
    cfg, task: Path, resume: int | None = None, fresh: bool = False, change: dict | None = None
) -> list[str]:
    """``fresh``: a new round that starts from nothing (``repo-factory build --fresh``); by default a
    new round continues an earlier round's stopped build (v5 ``carry_from``). ``change``: a
    revise round (Ask for changes; ``build.change``): ``repo-factory revise <task> --note-file
    <task>/.den/changes/<k>.md --mode <m> --origin <o>``, with ``--resume <round>`` on a
    resume (``build.revise_command`` replaces ``repo-factory revise``, for tests)."""
    b = cfg["build"]
    if change:
        base = list(b.get("revise_command") or [*BRB, "revise"])
        args = [
            str(task),
            "--note-file",
            str(change["file"]),
            "--mode",
            change.get("mode") or "auto",
            "--origin",
            change.get("origin") or "cli",
        ]
        if resume is not None:
            args += ["--resume", str(int(resume))]
        return base + args
    base = list(b.get("command") or [*BRB, "build"])
    args = [
        str(task),
        "--policy",
        str(policy_of(b)),
        "--owner",
        b.get("owner") or "none",
        "--harness",
        b.get("harness") or "claude-code",
    ]
    cap = b.get("max_usd")
    if cap is not None and not isinstance(cap, bool) and isinstance(cap, (int, float)):
        if cap <= 0:  # the schema refuses it before a build starts; never "no cap" here
            raise ValueError("build.max_usd: " + NOT_A_CAP)
        args += ["--max-cost", f"{float(cap):g}"]
    if resume is not None:
        args += ["--resume", str(int(resume))]
    elif fresh:
        args += ["--fresh"]
    return base + args


FLOWS = ("v5", "simple", "lean")


def policy_of(b: dict) -> Path:
    """The policy a new build runs: ``build.policy``; with ``build.flow: simple`` or
    ``lean`` and the policy left at its default (``policies/v5``), that flow's
    (``policies/simple``, BF-DESIGN; ``policies/lean``, track LEAN). A policy set by hand is
    used as it is."""
    from ..resources import POLICIES

    policy = Path(b["policy"]).expanduser()
    flow = str(b.get("flow") or "v5")
    if flow not in FLOWS:
        raise ValueError(f"build.flow: one of {', '.join(FLOWS)} (not {flow!r})")
    try:
        default = policy.resolve() == (POLICIES / "v5").resolve()
    except OSError:
        default = False
    return POLICIES / flow if flow != "v5" and default else policy


def flow_of(cmd: list[str]) -> str:
    """The flow a build command runs: the ``flow:`` of its ``--policy``'s rules
    (``simple``, ``lean``), else ``v5``."""
    import yaml

    try:
        policy = Path(cmd[cmd.index("--policy") + 1])
        rules = yaml.safe_load((policy / "rules.yaml").read_text()) or {}
    except (ValueError, IndexError, OSError, yaml.YAMLError):
        return "v5"
    flow = str(rules.get("flow") or "")
    return flow if flow in FLOWS else "v5"


def start(
    cfg, task: Path, resume: int | None = None, fresh: bool = False, change: dict | None = None
) -> dict:
    """Spawn the build (or the revise round, ``change``); ``{"pid", "started", "resume"}``."""
    import json

    task = Path(task)
    den = task / ".den"
    den.mkdir(parents=True, exist_ok=True)
    cmd = command(cfg, task, resume, fresh, change)
    env = build_env(cfg)
    if change:  # revise.models and revise.max_usd (unset: none)
        rv = cfg.get("revise") or {}
        env = {
            **(env or os.environ),
            "REPO_FACTORY_REVISE": json.dumps(
                {"max_usd": rv.get("max_usd"), "models": rv.get("models") or {}}
            ),
        }
    prior = (load_state(task) or {}).get("started")  # the newest round before this one
    p = proc.spawn_detached(
        cmd, cwd=task, log=den / "build.log", env=env, banner=f"repo-factory loop: {' '.join(cmd)}"
    )
    return {
        "pid": p.pid,
        "started": time.time(),
        "resume": resume,
        "cmd": cmd,
        "prior_started": prior,
    }


def outcome(task: Path, rec: dict) -> dict:
    """What became of the build ``rec`` once its process is gone: ``{"status": ok|failed,
    "round", "cost_usd", "error", "resumable"}``. ``ok`` needs a ``build_finished`` ok with
    a delivered branch, for a build started by this record (a fresh build's
    ``build_started`` after the spawn, or the resumed round)."""
    st = load_state(task, build=rec.get("round")) if rec.get("round") else load_state(task)
    fresh = is_fresh(st, rec)
    rnd = st.get("build") if st else rec.get("round")
    cost = st.get("cost_usd") if st else None
    if fresh and st["status"] == "ok" and (st.get("delivered") or {}).get("branch"):
        return {
            "status": "ok",
            "round": rnd,
            "cost_usd": cost,
            "error": None,
            "resumable": False,
            "interrupted": False,
        }
    err = (st or {}).get("error") or (
        "the build process exited before it started" if not fresh else f"status {st['status']}"
    )
    return {
        "status": "failed",
        "round": rnd if fresh else None,
        "cost_usd": cost,
        "error": err,
        "resumable": bool(fresh and rnd and resumable(task, rnd)),
        # killed without a build_finished (a power loss, SIGKILL): a crash, not a failure
        "interrupted": bool(fresh and st["status"] in ("interrupted", "running")),
        # v5: the stage that failed and how (auth: the Claude login; a retry can't help
        # until the owner logs in again)
        "stage": (st or {}).get("failed_stage") if fresh else None,
        "failure": (st or {}).get("failure") if fresh else None,
    }


def is_fresh(st: dict | None, rec: dict) -> bool:
    """The round state ``st`` belongs to the build ``rec`` started: its resumed round, or a
    round that began with its spawn (give or take 5 s) and is not the round that was the
    newest when it was spawned (one that began a moment before, as a fast fake round does)."""
    if st is None:
        return False
    if rec.get("round") is not None:
        return True
    started = st.get("started") or 0
    return started >= rec.get("started", 0) - 5 and (
        rec.get("prior_started") is None or started != rec["prior_started"]
    )


STOPPED = "stopped.json"  # in <den>/v5/r<n>/: the owner stopped this round (Stop round)


def resumable(task: Path, rnd: int, stopped_ok: bool = False) -> bool:
    """v5 saved a state for the round and it isn't finished. A round the owner stopped
    (``stopped.json`` beside its state) is not, unless ``stopped_ok`` (their Resume)."""
    import json

    root = Path(task) / ".den" / "v5" / f"r{int(rnd)}"
    p = root / "state.json"
    if not p.exists() or (not stopped_ok and (root / STOPPED).exists()):
        return False
    try:
        return "deliver" not in (json.loads(p.read_text()).get("done") or [])
    except (OSError, ValueError):
        return False


def current_round(task: Path) -> int | None:
    st = load_state(task)
    return st.get("build") if st else None


# -- one build's workers, and nothing else (RC: Stop round, Close) ----------------------------
# The process primitives live in repo_factory/proc.py (S-9); a build's marker is its task folder.
KILL_GRACE_S = proc.KILL_GRACE_S
_children = proc._children  # this process's own builds (reaped by poll)
_stat, _same, tree, finish, reap = proc._stat, proc.same, proc.tree, proc.finish, proc.reap


def alive(pid: int | None, task: Path | None = None) -> bool:
    """The build process is running (not a zombie, and, when ``task`` is given, still the
    process that was started for it: a recycled pid is not)."""
    return proc.alive(pid, task)


def is_build(pid: int | None, task: Path) -> bool:
    """``pid`` runs and is ``task``'s build (its command line names the task folder)."""
    return proc.is_job(pid, task)


def signal_build(pid: int, sig: int, task: Path | None = None) -> list[dict]:
    """Send ``sig`` to one build: its process group and each process it started, nothing
    else; with ``task``, only when ``pid`` is still that task's build."""
    return proc.signal_tree(pid, sig, task)


def stop(pid: int | None, grace_s: float = 20.0, task: Path | None = None) -> bool:
    """SIGTERM the build's tree, SIGKILL what is left after ``grace_s``; waits. ``repo-factory loop
    stop`` waits for this; an owner's press uses ``kill``."""
    return proc.stop(pid, grace_s, task)


def kill(pid: int | None, task: Path, grace_s: float = KILL_GRACE_S) -> list[dict]:
    """The owner's stop of one build, without waiting (a detached reaper finishes it)."""
    return proc.kill(pid, task, grace_s)


if __name__ == "__main__":  # python -m repo_factory.loop.builds reap S JSON (reapers started
    if len(sys.argv) == 4 and sys.argv[1] == "reap":  # before S-9 still name this module)
        import json as _json

        reap(float(sys.argv[2]), _json.loads(sys.argv[3]))
