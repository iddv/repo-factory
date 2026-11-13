"""Background jobs: start a process that outlives the request (or the loop's tick) that asked
for it, tell whether it still runs, stop it, and remember it across a restart (CR-PLAN S-9;
review A-6). Moved from ``loop/builds.py``, whose careful helpers every other spawner now
shares:

- ``spawn_detached``: its own session (so its process group is its own), stdin closed, output
  appended to a log, reaped by a waiter thread (no zombies); ``ident`` is what a record keeps;
- ``alive``: running, not a zombie, and (with ``marker``) still the process that was started:
  its command line names the marker, so a recycled pid never is;
- ``signal_tree``/``stop``/``kill``: signal one job, its process group and every process it
  started, each identified by its start time, nothing else; ``stop`` waits, ``kill`` leaves a
  detached reaper (``python -m repo_factory.proc reap``);
- records: ``<home>/jobs/<kind>/<id>.json`` (``write_record``, ``read_record``, ``records``),
  so a restarted server still knows its jobs and their pids; ``record_alive`` reads any record
  that keeps ``ident``'s ``pid`` and ``start``, wherever it is stored;
- ``thread``: a job that is a thread of this process (a GitHub step, a chat reply) records
  ``me()``, so after a restart its record reads as dead, not as running forever;
- ``on_spawner``: every process that outlives the thread asking for it starts from one
  thread that lives as long as this process (see there);
- ``detached``: the jobs this process started to outlive it (and ``kill``'s reapers), which
  the server's stop leaves running; every other child of the server is ended with it.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path

KILL_GRACE_S = 10.0
_children: dict[int, subprocess.Popen] = {}  # this process's own jobs (reaped by poll)


# -- the spawner --------------------------------------------------------------------------------
# PR_SET_PDEATHSIG (bubblewrap's --die-with-parent, ``unshare --kill-child``) fires when the
# *thread* that forked the child exits, not the process. A server answers each request on a
# worker thread that retires when idle, so a sandbox started there was SIGKILLed some seconds
# after its request (Open in Claude Code on live: code -9, 27 s in). Anything that must outlive
# its request is forked by the spawner instead: one thread, started once, that never exits.
_spawn_q: queue.Queue = queue.Queue()
_spawner: list = [None, None]  # [thread, pid it was started in]
_spawner_lock = threading.Lock()


def _spawn_forever() -> None:
    while True:
        fn, args, kw, fut = _spawn_q.get()
        if not fut.set_running_or_notify_cancel():
            continue
        try:
            fut.set_result(fn(*args, **kw))
        except BaseException as e:  # noqa: BLE001 - raised in the caller
            fut.set_exception(e)


def spawner() -> threading.Thread:
    """The spawner thread, started on first use (and again in a forked child, which has none)."""
    with _spawner_lock:
        t, pid = _spawner
        if t is None or pid != os.getpid() or not t.is_alive():
            t = threading.Thread(target=_spawn_forever, daemon=True, name="factory-spawner")
            t.start()
            _spawner[:] = [t, os.getpid()]
        return t


def on_spawner(fn, *args, **kw):
    """``fn(*args, **kw)`` run on the spawner thread; its result, or its exception raised here."""
    if threading.current_thread() is spawner():
        return fn(*args, **kw)
    fut: Future = Future()
    _spawn_q.put((fn, args, kw, fut))
    return fut.result()


# -- starting ---------------------------------------------------------------------------------
def spawn_detached(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    log: Path | None = None,
    env: dict | None = None,
    banner: str | None = None,
) -> subprocess.Popen:
    """Start ``cmd`` in a session of its own, stdin closed, stdout and stderr appended to
    ``log`` (else discarded), after a ``banner`` line. This process reaps it (``alive``).
    Forked by the spawner: it outlives the request that asked for it."""
    return on_spawner(_spawn_detached, cmd, cwd=cwd, log=log, env=env, banner=banner)


def _spawn_detached(cmd, *, cwd, log, env, banner) -> subprocess.Popen:
    if log is not None:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        out = open(log, "ab")
    else:
        out = subprocess.DEVNULL
    try:
        if banner and log is not None:
            out.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {banner}\n".encode())
            out.flush()
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    finally:
        if log is not None:
            out.close()
    _children[p.pid] = p
    threading.Thread(target=p.wait, daemon=True, name=f"reap-{p.pid}").start()
    return p


def spawn_terminal(
    cmd: list[str], *, terminal: int, cwd: Path | None = None, env: dict | None = None, pass_fds=()
) -> subprocess.Popen:
    """Start ``cmd`` in a session of its own on a pseudo-terminal (``terminal``: its other end,
    for stdin, stdout and stderr), keeping ``pass_fds`` open in it. The caller waits for it
    (Open in Claude Code's ``term.pty``: its waiter thread reaps it). Forked by the spawner:
    the sandbox's ``--die-with-parent`` is tied to that thread, not to the caller's."""
    return on_spawner(
        subprocess.Popen,
        cmd,
        cwd=cwd,
        env=env,
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        pass_fds=tuple(pass_fds),
        start_new_session=True,
    )


def detached() -> list[int]:
    """The pids of this process's detached jobs still running (``spawn_detached``'s and
    ``kill``'s reapers): what the server's stop leaves alone (builds, fixes, pre-push checks are
    adopted by the next start; a reaper finishes its own kill). Any other child of the server
    (a terminal, a ``claude -p`` of a tick or a request, a ``git``) ends with it."""
    return [pid for pid, p in list(_children.items()) if p.poll() is None]


def ident(pid: int) -> dict:
    """What a record keeps of a job's process: ``{"pid", "start"}`` (``record_alive``)."""
    return {"pid": int(pid), "start": start_time(int(pid))}


def me() -> dict:
    """``ident`` of this process: for a job that is one of its threads."""
    return ident(os.getpid())


def thread(fn, name: str) -> threading.Thread:
    """Run ``fn`` on a daemon thread of this process (its record keeps ``me()``)."""
    t = threading.Thread(target=fn, daemon=True, name=name)
    t.start()
    return t


# -- is it still ours ---------------------------------------------------------------------------
def _stat(pid: int) -> tuple[int, int, int, str] | None:
    """``(ppid, pgid, start time, state)`` from ``/proc/<pid>/stat``, or None."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            rest = fh.read().rsplit(")", 1)[1].split()
        return int(rest[1]), int(rest[2]), int(rest[19]), rest[0]
    except (OSError, ValueError, IndexError):
        return None


def start_time(pid: int) -> int | None:
    """The process's start time (clock ticks since boot), which a recycled pid never shares."""
    st = _stat(int(pid))
    return st[2] if st else None


def cwd(pid: int) -> Path | None:
    """The process's working directory, or None when it is gone or not ours to read."""
    try:
        return Path(os.readlink(f"/proc/{int(pid)}/cwd"))
    except (OSError, ValueError):
        return None


def cmdline(pid: int) -> str | None:
    try:
        return (
            Path(f"/proc/{int(pid)}/cmdline")
            .read_bytes()
            .replace(b"\0", b" ")
            .decode(errors="replace")
        )
    except OSError:
        return None


def alive(pid: int | None, marker: str | Path | None = None) -> bool:
    """The process runs (not a zombie) and, with ``marker``, its command line names it."""
    if not pid:
        return False
    p = _children.get(pid)
    if p is not None:
        return p.poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    proc = Path(f"/proc/{pid}")
    if proc.exists():
        try:
            if ") Z" in (proc / "stat").read_text():
                return False
            if marker is not None:
                cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                return str(marker) in cmd
        except OSError:
            return False
    return True


def running(pid: int | None, start: int | None = None) -> bool:
    """The job recorded as ``pid`` (and, when known, ``start``, its start time) still runs:
    alive, and the same process, not one that got its pid later. A record written before
    start times were kept (``start`` None) falls back to ``alive``."""
    if not alive(pid):
        return False
    return start is None or start_time(int(pid)) == start


def is_job(pid: int | None, marker: str | Path) -> bool:
    """``pid`` runs, is no zombie, and its command line names ``marker``: the check before any
    signal."""
    if not pid:
        return False
    cmd = cmdline(int(pid))
    st = _stat(int(pid))
    return cmd is not None and st is not None and st[3] != "Z" and str(marker) in cmd


# -- stopping ----------------------------------------------------------------------------------
def tree(pid: int) -> list[dict]:
    """``pid`` and every process descended from it, each ``{"pid", "pgid", "start"}``."""
    procs = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            st = _stat(int(d))
            if st is not None and st[3] != "Z":
                procs[int(d)] = st
    kids: dict[int, list[int]] = {}
    for p, st in procs.items():
        kids.setdefault(st[0], []).append(p)
    out, todo, seen = [], [int(pid)], set()
    while todo:
        p = todo.pop()
        if p in seen or p not in procs:
            continue
        seen.add(p)
        out.append({"pid": p, "pgid": procs[p][1], "start": procs[p][2]})
        todo += kids.get(p, [])
    return out


def same(p: dict) -> bool:
    st = _stat(p["pid"])
    return st is not None and st[2] == p["start"] and st[3] != "Z"


def _signal(procs: list[dict], sig: int) -> None:
    for p in procs:
        if same(p):
            try:
                os.kill(p["pid"], sig)
            except (ProcessLookupError, PermissionError):
                pass


def signal_tree(pid: int, sig: int, marker: str | Path | None = None) -> list[dict]:
    """Send ``sig`` to one job: its process group and each process it started, nothing else.
    With ``marker``, only when ``pid`` is still that job. Returns the processes signalled."""
    if marker is not None and not is_job(pid, marker):
        return []
    procs = tree(pid)
    if not procs:
        return []
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    _signal(procs, sig)
    return procs


def finish(procs: list[dict]) -> None:
    """SIGKILL whatever of ``procs`` still runs (the same processes: start times match)."""
    _signal(procs, signal.SIGKILL)


def stop(pid: int | None, grace_s: float = 20.0, marker: str | Path | None = None) -> bool:
    """SIGTERM the job's tree, SIGKILL what is left after ``grace_s``; waits. True when
    something was running."""
    if not pid or not alive(pid) or (marker is not None and not is_job(pid, marker)):
        return False
    procs = signal_tree(pid, signal.SIGTERM, marker)
    if not procs:
        return False
    deadline = time.time() + grace_s
    while time.time() < deadline and any(same(p) for p in procs):
        alive(pid)  # reaps our own child
        time.sleep(0.2)
    finish(procs)
    alive(pid)
    return True


def kill(pid: int | None, marker: str | Path, grace_s: float = KILL_GRACE_S) -> list[dict]:
    """Stop one job without waiting: SIGTERM now (after the marker check), and a detached
    reaper SIGKILLs what is left after ``grace_s``. Returns the processes signalled."""
    if not pid:
        return []
    procs = signal_tree(int(pid), signal.SIGTERM, marker)
    if procs:
        r = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "repo_factory.proc",
                "reap",
                f"{float(grace_s):g}",
                json.dumps(procs),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        _children[r.pid] = r  # detached: a server's stop leaves it be
        threading.Thread(target=r.wait, daemon=True, name=f"reap-{r.pid}").start()
        alive(int(pid))
    return procs


def reap(grace_s: float, procs: list[dict]) -> None:
    """The reaper ``kill`` leaves behind."""
    deadline = time.time() + grace_s
    while time.time() < deadline and any(same(p) for p in procs):
        time.sleep(0.2)
    finish(procs)


# -- records -----------------------------------------------------------------------------------
def record_path(home: Path, kind: str, job_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(job_id))
    return Path(home) / "jobs" / kind / f"{safe}.json"


def write_record(home: Path, kind: str, job_id: str, rec: dict) -> Path:
    """Write the job's record atomically: ``{"pid", "start", "cmd", "started", ...}``."""
    path = record_path(home, kind, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True))
    os.replace(tmp, path)
    return path


def read_record(home: Path, kind: str, job_id: str) -> dict | None:
    try:
        return json.loads(record_path(home, kind, job_id).read_text())
    except (OSError, ValueError):
        return None


def records(home: Path, kind: str) -> dict[str, dict]:
    out = {}
    for f in sorted((Path(home) / "jobs" / kind).glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
    return out


def record_alive(rec: dict | None) -> bool:
    """The recorded job is still the process that was started: its pid runs with the same
    start time (a recycled pid has another)."""
    if not rec or not rec.get("pid"):
        return False
    st = _stat(int(rec["pid"]))
    if st is None or st[3] == "Z":
        return False
    return rec.get("start") is None or st[2] == rec["start"]


if __name__ == "__main__":  # python -m repo_factory.proc reap S JSON
    if len(sys.argv) == 4 and sys.argv[1] == "reap":
        reap(float(sys.argv[2]), json.loads(sys.argv[3]))
