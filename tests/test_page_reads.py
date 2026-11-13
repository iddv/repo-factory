"""A page opens on reads that never wait on a process or the network (SL).

Every ``GET`` the pages make when they open is asked twice on a world with a maintained repo
whose ``gh`` is a real subprocess (a script here) and ``maintain.cache_s`` 0, so every answer
is already old: once to fill the caches, then on a new App (a restart). The second time, an
audit hook watches the request's own thread: no process started, no connection opened except
to loopback. GitHub is asked again, in the background. And the release gate of a build started
by hand (which runs ``git``) is read from its file after a restart, not run again.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from repo_factory.gh import Gh
from repo_factory.maintain import github as mgh
from repo_factory.ui import api

from .loop_common import make_loop

GH = """import json, os, sys
a = sys.argv[1:]
open(os.environ["PAGE_GH_OUT"], "a").write(json.dumps(a) + "\\n")
if a[:3] == ["api", "-i", "user"]:
    print("HTTP/2.0 200 OK")
    print("Content-Type: application/json")
    print()
    print(json.dumps({"login": "owner"}))
elif a[:1] == ["api"] and a[1].startswith("repos/") and "-q" in a:
    print(a[1][len("repos/"):])
else:
    sys.stderr.write("HTTP 404: Not Found\\n")
    sys.exit(1)
"""

# what the pages ask for when they open (the shell's own reads, Settings, a build's box)
PAGE_GETS = [
    "/api/state",
    "/api/header",
    "/api/projects",
    "/api/maintain/summary",
    "/api/maintain/lessons",
    "/api/maintain/repos",
    "/api/capabilities",
    "/api/extensions",
    "/api/models",
    "/api/prompts",
    "/api/spend/breakdown",
    "/api/loop/settings",
    "/api/projects/settings",
    "/api/github/user",
    "/api/github/app",
    "/api/term",
]

_WATCH: dict = {"thread": None, "seen": []}


def _hook(event, args):
    if _WATCH["thread"] != threading.get_ident():
        return
    if event in ("subprocess.Popen", "os.posix_spawn", "os.fork", "os.exec"):
        _WATCH["seen"].append((event, repr(args)[:120]))
    elif event == "socket.connect":
        addr = args[1]
        if isinstance(addr, tuple) and addr and addr[0] in ("127.0.0.1", "::1", "localhost"):
            return
        if isinstance(addr, str | bytes):  # a unix socket
            return
        _WATCH["seen"].append((event, repr(addr)[:120]))


sys.addaudithook(_hook)


@pytest.fixture
def world(tmp_path, monkeypatch):
    script = tmp_path / "gh.py"
    script.write_text(GH)
    out = tmp_path / "gh.jsonl"
    monkeypatch.setenv("PAGE_GH_OUT", str(out))
    lp = make_loop(tmp_path)
    lp._gh = Gh(cmd=[sys.executable, str(script)])
    lp.cfg.data["maintain"]["cache_s"] = 0  # every answer is old at once
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper", "one_liner": "Allotment plots and rents."},
        publish={"repo": "o/plots", "url": "https://github.com/o/plots"},
    )
    return lp, i["id"], out


def app_for(lp):
    a = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a.loop, a.loop_cfg = (lambda: lp), (lambda: lp.cfg)
    return a


def settle(timeout=30.0):
    """Wait for the background reads (``factory-read …`` threads) to finish."""
    end = time.monotonic() + timeout
    for t in [t for t in threading.enumerate() if t.name.startswith("factory-read")]:
        t.join(max(0.0, end - time.monotonic()))


def gh_calls(out: Path) -> list:
    return [json.loads(x) for x in out.read_text().splitlines()] if out.exists() else []


def test_no_page_waits_on_a_process_or_the_network_after_the_first_open(world):
    lp, iid, out = world
    gets = [*PAGE_GETS, f"/api/maintain/{iid}"]
    first = app_for(lp)
    for path in gets:  # the first open ever fills the caches
        code, _, _ = api.handle("GET", path, None, None, app=first)
        assert code < 500, path
    settle()
    asked = len(gh_calls(out))
    assert asked >= 3  # the user, the repo's reach, its policy
    assert (lp.cfg.home / "maintain" / "reads.json").exists()

    again = app_for(lp)  # a restart: only the files are kept
    times = {}
    _WATCH["seen"], _WATCH["thread"] = [], threading.get_ident()
    try:
        for path in gets:
            t = time.monotonic()
            code, _, body = api.handle("GET", path, None, None, app=again)
            times[path] = time.monotonic() - t
            assert code < 500, path
            assert not _WATCH["seen"], (path, _WATCH["seen"])
    finally:
        _WATCH["thread"] = None
    d = json.loads(api.handle("GET", "/api/maintain/summary", None, None, app=again)[2])
    assert d["identity"]["github"]["login"] == "owner"  # the last answer, not "asking"
    assert d["identity"]["github"]["reach"] == [1, 1]
    settle()
    assert len(gh_calls(out)) > asked  # and GitHub was asked again, behind it
    assert max(times.values()) < 5, times  # nothing waited on GitHub (2 s at most)


def test_a_read_never_seen_waits_a_moment_then_says_it_is_still_asking(world, monkeypatch):
    lp, iid, out = world
    gate = threading.Event()
    monkeypatch.setattr(mgh.Reader, "first_wait", 0.05)
    a = app_for(lp)
    rd = a.maint_reader()
    v = rd.cached(("o/plots", "slow"), lambda: gate.wait(10) and "late", default=mgh.PENDING)
    assert v is mgh.PENDING
    gate.set()
    settle()
    assert rd.cached(("o/plots", "slow"), lambda: "never asked", default=mgh.PENDING) == "late"
    monkeypatch.setattr(a, "maint_identity_read", lambda: gate.wait(10) or {})
    gate.clear()
    rd._hits.clear()
    assert a.maint_identity()["pending"] is True
    gate.set()
    settle()


def test_the_hand_gate_is_kept_beside_the_build_and_read_after_a_restart(world, monkeypatch):
    lp, _, _ = world
    from repo_factory.loop import gate

    d = Path(lp.cfg.tasks_dir) / "by-hand"
    den = d / ".den"
    den.mkdir(parents=True)
    (den / "events.jsonl").write_text('{"type": "build_started"}\n')
    runs = []
    monkeypatch.setattr(
        gate, "evaluate", lambda task: runs.append(task) or {"verdict": "ready", "reasons": []}
    )
    assert app_for(lp).hand_gate(d)["verdict"] == "ready"
    assert app_for(lp).hand_gate(d)["verdict"] == "ready"  # a restart: from the file
    assert len(runs) == 1
    with (den / "events.jsonl").open("a") as f:  # a new event: judged again
        f.write('{"type": "x"}\n')
    app_for(lp).hand_gate(d)
    assert len(runs) == 2


def test_the_hook_sees_a_process_and_a_connection_on_the_request_thread():
    """The hook itself: it would catch a handler that ran ``gh`` or opened a socket."""
    import subprocess

    _WATCH["seen"], _WATCH["thread"] = [], threading.get_ident()
    try:
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        with socket.socket() as s:  # the event socket.connect raises, no packet
            sys.audit("socket.connect", s, ("192.0.2.1", 443))
            sys.audit("socket.connect", s, ("127.0.0.1", 8765))  # loopback: allowed
    finally:
        _WATCH["thread"] = None
    kinds = [k for k, _ in _WATCH["seen"]]
    assert "subprocess.Popen" in kinds and kinds.count("socket.connect") == 1
