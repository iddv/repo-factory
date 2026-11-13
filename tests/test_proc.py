"""repo_factory/proc.py (CR-PLAN S-9): one way to start a background job, tell whether it still
runs, stop it, and remember it across a restart. Real processes (sleep), loopback-free."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from repo_factory import proc
from repo_factory.loop import builds

SLEEPER = [sys.executable, "-c", "import time, sys; time.sleep(60)"]


def wait_exec(pid, marker, s=10.0):
    """Until /proc shows the job's own command line. Popen returns once the child has started
    its exec (vfork), but the kernel sets the new argument range a moment later: read at
    once, /proc/<pid>/cmdline can still be empty (seen under CI load)."""
    end = time.time() + s
    while time.time() < end and not proc.is_job(pid, marker):
        time.sleep(0.02)
    return proc.is_job(pid, marker)


def wait_gone(pid, s=10.0):
    end = time.time() + s
    while time.time() < end and proc.alive(pid):
        time.sleep(0.05)
    return not proc.alive(pid)


def test_a_detached_job_runs_in_its_own_session_and_logs_after_a_banner(tmp_path):
    log = tmp_path / "logs" / "job.log"
    p = proc.spawn_detached([*SLEEPER, "marker-1"], cwd=tmp_path, log=log, banner="hello")
    try:
        assert proc.alive(p.pid) and proc.alive(p.pid, "marker-1")
        assert wait_exec(p.pid, "marker-1") and not proc.is_job(p.pid, "another-job")
        assert os.getpgid(p.pid) == p.pid != os.getpgid(0)  # a session of its own
        assert "=== " in log.read_text() and log.read_text().rstrip().endswith("hello")
    finally:
        assert proc.stop(p.pid, grace_s=5, marker="marker-1")
    assert not proc.alive(p.pid)


def test_stop_refuses_a_pid_that_is_no_longer_the_job(tmp_path):
    p = proc.spawn_detached([*SLEEPER, "mine"], log=tmp_path / "l")
    try:
        assert wait_exec(p.pid, "mine")
        assert not proc.stop(p.pid, grace_s=1, marker="someone-elses")
        assert proc.alive(p.pid)
        assert proc.signal_tree(p.pid, signal.SIGTERM, "someone-elses") == []
    finally:
        proc.stop(p.pid, grace_s=5)


def test_kill_signals_now_and_leaves_a_reaper(tmp_path):
    p = proc.spawn_detached(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
            "stubborn",
        ],
        log=tmp_path / "l",
    )
    assert wait_exec(p.pid, "stubborn")
    time.sleep(0.3)  # let it install its handler
    procs = proc.kill(p.pid, "stubborn", grace_s=0.5)
    assert procs and procs[0]["pid"] == p.pid
    assert wait_gone(p.pid, 15)  # SIGKILLed by the reaper


def test_the_detached_jobs_are_what_a_servers_stop_leaves_running(tmp_path):
    """A server's stop ends every child of the server but these: a detached
    job and a kill's reaper are listed while they run; an attached child never is."""
    job = proc.spawn_detached([*SLEEPER, "detached-job"], log=tmp_path / "l")
    attached = subprocess.Popen([*SLEEPER, "attached-child"])
    stubborn = proc.spawn_detached(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
            "stubborn-2",
        ],
        log=tmp_path / "l",
    )
    try:
        assert wait_exec(stubborn.pid, "stubborn-2")
        time.sleep(0.3)  # let it install its handler
        assert {job.pid, stubborn.pid} <= set(proc.detached())
        assert attached.pid not in proc.detached()
        proc.kill(stubborn.pid, "stubborn-2", grace_s=3)
        reapers = set(proc.detached()) - {job.pid, stubborn.pid}
        assert len(reapers) == 1 and "repo_factory.proc reap" in proc.cmdline(reapers.pop())
    finally:
        attached.kill()
        attached.wait()
        proc.stop(job.pid, grace_s=5)
    assert wait_gone(job.pid) and job.pid not in proc.detached()


def test_records_survive_a_restart_and_a_recycled_pid_is_not_the_job(tmp_path):
    p = proc.spawn_detached(SLEEPER, log=tmp_path / "l")
    try:
        rec = {"pid": p.pid, "start": proc.start_time(p.pid), "cmd": SLEEPER}
        path = proc.write_record(tmp_path, "onboard", "acme/app", rec)
        assert path == tmp_path / "jobs" / "onboard" / "acme_app.json"
        got = proc.read_record(tmp_path, "onboard", "acme/app")
        assert got == rec and proc.records(tmp_path, "onboard") == {"acme_app": rec}
        assert proc.record_alive(got)
        assert not proc.record_alive({**got, "start": got["start"] + 1})  # a recycled pid
    finally:
        proc.stop(p.pid, grace_s=5)
    assert not proc.record_alive(got)
    assert proc.read_record(tmp_path, "onboard", "none") is None


def test_the_loops_build_helpers_are_the_same_primitives():
    assert builds._children is proc._children and builds.KILL_GRACE_S == proc.KILL_GRACE_S
    assert builds.tree is proc.tree and builds.finish is proc.finish


def test_running_is_the_same_process_not_just_the_same_pid(tmp_path):
    p = proc.spawn_detached(SLEEPER, log=tmp_path / "l")
    try:
        s = proc.start_time(p.pid)
        assert proc.running(p.pid, s) and proc.running(p.pid)  # no start: as before
        assert not proc.running(p.pid, s + 1)  # a recycled pid
        assert not proc.running(None) and not proc.running(0, s)
    finally:
        proc.stop(p.pid, grace_s=5)
    assert not proc.running(p.pid, s)


# -- S-9 step 3: every background job of the product on proc ----------------------------------
def test_a_finished_job_is_reaped_without_anyone_asking(tmp_path):
    p = proc.spawn_detached([sys.executable, "-c", "pass"], log=tmp_path / "l")
    end = time.time() + 10
    while time.time() < end and proc._stat(p.pid) is not None:
        time.sleep(0.05)
    assert proc._stat(p.pid) is None  # gone, not a zombie


def test_a_thread_job_records_this_process_and_a_restart_ends_it():
    me = proc.me()
    assert me == {"pid": os.getpid(), "start": proc.start_time(os.getpid())}
    assert proc.record_alive(me)
    assert not proc.record_alive({**me, "start": me["start"] + 1})  # another server's
    ran = []
    proc.thread(lambda: ran.append(1), "test-job").join(5)
    assert ran == [1]


def test_the_pages_stop_signals_only_a_build_of_that_task(tmp_path):
    from repo_factory.ui.api import is_build_of

    task, other = tmp_path / "task", tmp_path / "other"
    task.mkdir()
    other.mkdir()
    named = proc.spawn_detached([*SLEEPER, str(task)], cwd=other, log=tmp_path / "a")
    inside = proc.spawn_detached([*SLEEPER, "factory-build-dot"], cwd=task, log=tmp_path / "b")
    stranger = proc.spawn_detached([*SLEEPER, "a-stranger"], cwd=other, log=tmp_path / "c")
    try:
        assert wait_exec(named.pid, str(task)) and wait_exec(stranger.pid, "a-stranger")
        assert wait_exec(inside.pid, "factory-build-dot")
        assert is_build_of(named.pid, task)  # its command line names the task
        assert is_build_of(inside.pid, task)  # repo-factory build . in the task folder
        assert not is_build_of(stranger.pid, task)  # a recycled build.pid: never
    finally:
        for p in (named, inside, stranger):
            proc.stop(p.pid, grace_s=5)


def test_a_github_step_a_restart_cut_off_reads_failed(tmp_path):
    from repo_factory.github_app import Store

    st = Store(tmp_path / "gh")
    st.set_job("refresh", "running", **proc.me(), started=time.time())
    assert st.job()["state"] == "running"
    gone = {**proc.me(), "start": proc.me()["start"] + 1}
    st.set_job("refresh", "running", **gone, started=time.time())
    j = st.job()
    assert j["state"] == "failed" and j["kind"] == "restart" and "restarted" in j["error"]


def test_a_projects_job_whose_pid_was_recycled_is_failed():
    from repo_factory.projects import jobs

    class Ledger:
        def __init__(self, rec):
            self.rec = rec

        def meta(self, key, value=None):
            return self.rec

        def metas(self, prefix):
            return {"p1": self.rec}

    rec = {"state": "running", **proc.me(), "started": time.time()}
    assert jobs.state(Ledger(rec), "charter", "p1")["state"] == "running"
    assert jobs.running(Ledger(rec), "charter") == ["p1"]
    rec = {**rec, "start": rec["start"] + 1}
    assert jobs.state(Ledger(rec), "charter", "p1")["state"] == "failed"
    assert jobs.running(Ledger(rec), "charter") == []


def test_the_other_front_doors_session_is_its_server_not_a_recycled_pid(tmp_path):
    """A record another live server runs (Open in Claude Code's ``runner``, as the chat's
    replies once were) is that server's while its pid and start time match, never a pid the
    system gave to someone else."""
    p = proc.spawn_detached([*SLEEPER, "other-door"], log=tmp_path / "l")
    try:
        runner = proc.ident(p.pid)
        assert proc.running(runner["pid"], runner["start"])
        assert not proc.running(runner["pid"], runner["start"] + 1)
    finally:
        proc.stop(p.pid, grace_s=5)


# The product's background jobs start through proc.spawn_detached and nowhere else. These run
# a child in the foreground of their own call (a model session, a command, a check) and stop it
# themselves; none outlives its caller.
FOREGROUND = {
    "harness/claude_code.py",
    "v5/cmdrunner.py",
    "v5/examrunner.py",
    "maintain/templates/check_agents_md.py",
    "proc.py",
}


def test_every_background_job_starts_through_proc():
    root = Path(proc.__file__).parent
    found = set()
    for f in root.rglob("*.py"):
        rel = f.relative_to(root).as_posix()
        if rel.startswith("lab/"):
            continue
        if "start_new_session=True" in f.read_text():
            found.add(rel)
    assert found == FOREGROUND, sorted(found - FOREGROUND)
