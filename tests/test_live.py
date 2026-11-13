"""The live feed (FE-PLAN §4.2, FE-3): ``/api/stream`` says what changed, never the data. One
watcher per process stats the state files (the ledger, each build's events,
the job records) and never reads the network or a model; a stdlib client reads the host
event and a change; a ninth stream is refused and the page polls; a place is given back when
its page goes away."""

import http.client
import json
import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest

from repo_factory import __version__
from repo_factory.events import EventLog
from repo_factory.ui import api, live
from repo_factory.ui.server import make_server

from .loop_common import make_loop

LIVE = Path(live.__file__)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """A fresh hub that ticks fast and pings often."""
    monkeypatch.setattr(live, "HUB", live.Hub())
    monkeypatch.setattr(live, "TICK_S", 0.05)
    monkeypatch.setattr(live, "PING_S", 0.3)


@pytest.fixture
def app(tmp_path):
    lp = make_loop(tmp_path)
    lp.cfg.tasks_dir.mkdir(parents=True, exist_ok=True)
    return api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)


def build(app, name: str) -> EventLog:
    d = app.root / name
    d.mkdir(parents=True)
    (d / "spec.md").write_text(f"# {name}\n")
    log = EventLog(d / ".den" / "events.jsonl", name, 1)
    log.emit("build_started", pid=4194311, pipeline="v5", policy="v5.0")
    return log


def events(chunks) -> list[tuple[str, dict]]:
    out = []
    for c in chunks:
        m = re.search(rb"event: (\w+)\ndata: (.*)\n\n", c)
        if m:
            out.append((m.group(1).decode(), json.loads(m.group(2))))
    return out


def next_change(it, wait_s: float = 10.0) -> list[tuple[str, dict]]:
    """The stream's next change events, skipping the pings a slow watcher lets through: on a
    busy runner the watcher can take longer than PING_S to see a file move."""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        got = events([next(it)])
        if got:
            return got
    raise AssertionError(f"no change within {wait_s}s")


def test_the_watcher_names_what_changed(app):
    log = build(app, "inv")
    w = live.Watcher(app)
    assert w.changes() is None  # the baseline
    assert w.changes() is None  # nothing moved
    log.emit("v5_stage", stage="scope", status="started")
    assert w.changes() == {"kinds": ["builds"], "keys": ["inv"]}
    build(app, "new")
    assert w.changes() == {"kinds": ["builds"], "keys": ["new"]}
    app.loop().add("Freelancers lose track of invoices")
    c = w.changes()
    assert {"loop", "ideas", "builds"} <= set(c["kinds"]) and c["keys"] == []
    for gone in ("chat", "term"):  # folders nothing reads any more
        d = Path(app.loop_cfg().home) / gone / "t1"
        d.mkdir(parents=True)
        (d / "x.json").write_text("{}\n")
        assert w.changes() is None, gone
    jobs = Path(app.loop_cfg().home) / "jobs" / "charter"
    jobs.mkdir(parents=True)
    (jobs / "x.json").write_text("{}")
    assert w.changes()["kinds"] == ["jobs"]


def test_a_stream_says_its_version_then_each_change(app):
    log = build(app, "inv")
    it = api.stream("/api/stream", app=app)
    assert next(it) == b"retry: 3000\n\n"
    assert events([next(it)]) == [("host", {"version": __version__})]
    assert next(it) == b": ping\n\n"  # nothing changed within PING_S
    log.emit("v5_stage", stage="scope", status="started")
    got = next_change(it)
    assert got == [("change", {"kinds": ["builds"], "keys": ["inv"], "seq": 1})]
    it.close()
    assert live.HUB.streams == {}
    assert api.stream("/api/other", app=app) is None


def test_a_reconnect_gets_what_it_missed(app):
    log = build(app, "inv")
    first = api.stream("/api/stream", app=app)
    next(first), next(first)
    log.emit("v5_stage", stage="scope", status="started")
    assert next_change(first)[0][1]["seq"] == 1
    log.emit("v5_stage", stage="scope", status="done")
    deadline = time.time() + 10
    while live.HUB.seq < 2 and time.time() < deadline:
        time.sleep(0.02)
    first.close()
    again = api.stream("/api/stream", "last=1", app=app)
    next(again), next(again)
    assert [e["seq"] for _, e in next_change(again)] == [2]
    again.close()


def test_a_change_between_opening_and_the_first_read_is_sent_not_lost(app):
    """The stream's start is fixed when it opens. It used to be taken when the client first
    read past the host line, so a change published in between was skipped: main went red on
    a loaded runner with the first change read as seq 2, or a ping where seq 1 should be."""
    it = api.stream("/api/stream", app=app)
    next(it), next(it)  # retry, host
    live.HUB.publish({"kinds": ["builds"], "keys": ["inv"]})
    assert events([next(it)]) == [("change", {"kinds": ["builds"], "keys": ["inv"], "seq": 1})]
    it.close()
    it = api.stream("/api/stream", app=app)  # nothing read at all before the change
    live.HUB.publish({"kinds": ["jobs"], "keys": []})
    next(it), next(it)
    assert events([next(it)]) == [("change", {"kinds": ["jobs"], "keys": [], "seq": 2})]
    it.close()


def test_a_ninth_stream_is_refused_and_a_closed_one_gives_its_place_back(app, monkeypatch):
    monkeypatch.setattr(live, "MAX_STREAMS", 2)
    a, b = api.stream("/api/stream", app=app), api.stream("/api/stream", app=app)
    # the one past the places says busy and ends, with a 30 s retry: no 503 in the page's
    # console (QA B20, REV3 fix 12)
    busy = list(api.stream("/api/stream", app=app))
    assert busy[0] == b"retry: 30000\n\n"
    assert events(busy[1:])[0][0] == "busy"
    next(a)
    a.close()
    c = api.stream("/api/stream", app=app)
    # a stream nobody read for STALE_S gives its place up too
    monkeypatch.setattr(live, "STALE_S", 0.0)
    d = api.stream("/api/stream", app=app)
    for it in (b, c, d):
        it.close()
    deadline = time.time() + 5
    while live.HUB.thread is not None and time.time() < deadline:
        time.sleep(0.05)
    assert live.HUB.thread is None  # the watcher stops with the last one


def test_factory_ui_streams_to_a_client(app):
    log = build(app, "inv")
    srv = make_server(app.root, port=0, harness="fake", loop_home=app.loop_home)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    got: list = []

    def read():
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/stream", headers={"Accept": "text/event-stream"})
        r = c.getresponse()
        got.append((r.status, r.getheader("Content-Type"), r.getheader("Cache-Control")))
        buf = b""
        while len(events([p + b"\n\n" for p in buf.split(b"\n\n")])) < 2:
            line = r.fp.readline()
            if not line:
                break
            buf += line
        got.append(events([p + b"\n\n" for p in buf.split(b"\n\n")]))
        c.close()

    t = threading.Thread(target=read)
    t.start()
    deadline = time.time() + 5
    while not live.HUB.streams and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    log.emit("v5_stage", stage="scope", status="started")
    t.join(10)
    try:
        assert got[0] == (200, "text/event-stream; charset=utf-8", "no-store")
        assert got[1][0] == ("host", {"version": __version__})
        assert got[1][1][0] == "change" and got[1][1][1]["keys"] == ["inv"]
        # the guard holds: a foreign Host is refused before any stream opens
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/stream", headers={"Host": "evil.example"})
        assert c.getresponse().status == 400
        c.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_producer_reads_files_only(app, monkeypatch):
    """No request waits on the network or a model: the watcher stats local files and imports
    nothing that could reach either."""
    src = LIVE.read_text()
    for banned in (
        "urllib",
        "socket",
        "http.client",
        "subprocess",
        "requests",
        "httpx",
        "anthropic",
        "harness",
        "models",
        "github",
        "gh(",
    ):
        assert banned not in src, banned

    def refuse(*a, **k):
        raise AssertionError("the live feed opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    build(app, "inv")
    w = live.Watcher(app)
    w.changes()
    assert w.changes() is None
    assert os.environ.get("REPO_FACTORY_NO_NETWORK", "1")  # the suite's own guard is on as well
