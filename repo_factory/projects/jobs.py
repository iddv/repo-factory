"""A project's background jobs: a charter draft, an evolve run (PJ-PLAN.md §4.3).

No request waits on a model (PA's rule): a press starts ``repo-factory projects <what> <id>`` in a
process of its own and returns at once; the page polls the job's state, ``{state, since,
finished, error}``, kept in the ledger's ``meta`` under ``project_job:<what>:<id>``. A job
whose process died (``proc.record_alive``: a recycled pid is not it) is ``failed``.
``$REPO_FACTORY_PROJECTS_SPAWN=0`` (the tests) starts none."""

from __future__ import annotations

import os
import sys
import time

from .. import proc
from ..errors import LoopError

SPAWN_ENV = "REPO_FACTORY_PROJECTS_SPAWN"
WHATS = ("charter", "evolve")


def key(what: str, proj: str) -> str:
    return f"project_job:{what}:{proj}"


def alive(rec: dict) -> bool:
    """The job's process still runs: its pid, with its start time when the record has one."""
    return bool(rec.get("pid")) and proc.record_alive(rec)


def state(ledger, what: str, proj: str) -> dict:
    """The job's record, a dead ``running`` one reported as ``failed``; ``{"state": "none"}``
    when it never ran."""
    rec = dict(ledger.meta(key(what, proj)) or {})
    if not rec:
        return {"state": "none"}
    if rec.get("state") == "running" and rec.get("pid") and not alive(rec):
        rec.update(state="failed", error=rec.get("error") or "it stopped before it finished")
    return rec


def running(ledger, what: str) -> list[str]:
    """The projects whose ``what`` job runs now."""
    return [
        k
        for k, rec in ledger.metas(f"project_job:{what}:").items()
        if rec.get("state") == "running" and alive(rec)
    ]


def set_state(ledger, what: str, proj: str, **rec) -> dict:
    cur = dict(ledger.meta(key(what, proj)) or {})
    cur.update(rec)
    ledger.meta(key(what, proj), cur)
    return cur


def spawn(cfg, ledger, what: str, pid: str, *args: str, by: str = "repo-factory") -> dict:
    """Start ``repo-factory projects <what> <pid> [args]`` detached (its output in
    ``projects/<pid>.<what>.log``) and record it running; refused in words while one runs."""
    if what not in WHATS:
        raise ValueError(what)
    cur = state(ledger, what, pid)
    if cur.get("state") in ("running", "queued"):
        raise LoopError(
            f"a {what} job of {pid} is running (since "
            f"{time.strftime('%H:%M', time.localtime(cur.get('started') or 0))})"
        )
    if os.environ.get(SPAWN_ENV) == "0":
        return set_state(
            ledger, what, pid, state="queued", pid=None, started=time.time(), by=by, error=None
        )
    d = cfg.layout.projects
    d.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "repo_factory.cli",
        "projects",
        what,
        pid,
        "--home",
        str(cfg.home),
        *args,
    ]
    p = proc.spawn_detached(cmd, log=d / f"{pid}.{what}.log")
    return set_state(
        ledger,
        what,
        pid,
        state="running",
        **proc.ident(p.pid),
        started=time.time(),
        by=by,
        error=None,
        finished=None,
    )
