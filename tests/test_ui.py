"""``repo-factory ui``: the whole owner loop over HTTP against the fake harness (no tokens)."""

import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from repo_factory.ui.server import make_server, scan

from .auth_common import ui_bearer

FIXTURE = Path(__file__).parent / "fixtures" / "repo_task"


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_FACTORY_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setenv("REPO_FACTORY_ANSWER_TIMEOUT", "120")
    root = tmp_path / "root"
    shutil.copytree(FIXTURE, root / "textstats", ignore=shutil.ignore_patterns(".den", "out"))
    srv = make_server(root, port=0, harness="fake")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, root
    srv.shutdown()
    srv.server_close()
    for p, _ in srv.app.procs.values():  # never leave a build running
        if p.poll() is None:
            p.kill()


def get(base, path, **q):
    url = base + path + ("?" + urllib.parse.urlencode(q) if q else "")
    with urllib.request.urlopen(url, timeout=30) as r:
        body = r.read().decode()
        return json.loads(body) if "json" in r.headers["Content-Type"] else body


def post(base, path, body):
    req = urllib.request.Request(
        base + path,
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Origin": base, **ui_bearer()},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def wait_for(base, d, states, timeout=120):
    deadline = time.time() + timeout
    st = None
    while time.time() < deadline:
        st = get(base, "/api/events", dir=d)["state"]
        if st["state"] in states:
            return st
        assert st["state"] not in ("failed", "stopped"), (
            st,
            get(base, "/api/text", dir=d, what="log"),
        )
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {states}; last {st}")


def git(out, *a):
    return subprocess.run(["git", *a], cwd=out, capture_output=True, text=True).stdout.strip()


def test_page_is_served(ui):
    base, _ = ui
    html = get(base, "/")
    assert "<title>repo-factory</title>" in html and '<script type="module" src="static/v' in html
    assert re.search(
        r'src="static/v[^/"]+/app/app-[\w-]+\.js"', html
    )  # this version's address (S-3b, FE-4)
    assert get(base, "/api/info")["harness"] == "fake"


def test_stop(ui):
    base, root = ui
    d = str((root / "textstats").resolve())
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, "/api/stop", {"dir": d})
    assert e.value.code == 409
    # a build runs real Claude Code sessions: the fake harness never starts one (no spend
    # under "fake")
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, "/api/start", {"dir": d})
    assert e.value.code == 400 and "no fake harness" in json.loads(e.value.read())["error"]


def test_dir_outside_root_is_404(ui, tmp_path):
    base, root = ui
    outside = tmp_path / "elsewhere"
    shutil.copytree(FIXTURE, outside, ignore=shutil.ignore_patterns(".den", "out"))
    for path, q in (
        ("/api/events", {"dir": str(outside)}),
        ("/api/text", {"dir": str(root / "textstats" / ".." / ".." / "elsewhere"), "what": "spec"}),
        ("/api/text", {"dir": "/etc", "what": "spec"}),
    ):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base, path, **q)
        assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, "/api/start", {"dir": str(outside)})
    assert e.value.code == 404
    assert not (outside / ".den").exists()


def test_builds_grouping(ui):
    base, root = ui
    from repo_factory.events import EventLog

    def task(rel, *evs):
        d = root / rel
        d.mkdir(parents=True)
        (d / "spec.md").write_text(f"# {d.name}\n")
        log = EventLog(d / ".den" / "events.jsonl", d.name, 1)
        for t, f in evs:
            log.emit(t, **f)
        return d

    live = {"pid": __import__("os").getpid()}  # this process: alive
    task("group/asking", ("build_started", live), ("question", {"id": "q1", "text": "?"}))
    task("group/running", ("build_started", live))
    task(
        "delivered",
        ("build_started", live),
        ("delivered", {"node": 3}),
        ("build_finished", {"status": "ok"}),
    )
    task(
        "accepted",
        ("build_started", live),
        ("delivered", {"node": 3}),
        ("build_finished", {"status": "ok"}),
        ("verdict", {"verdict": "accept"}),
    )
    task(
        "rejected",
        ("build_started", live),
        ("delivered", {"node": 3}),
        ("build_finished", {"status": "ok"}),
        ("verdict", {"verdict": "reject"}),
    )
    task(
        "failed", ("build_started", live), ("build_finished", {"status": "error", "error": "boom"})
    )
    task("stopped", ("build_started", {"pid": 2**22 + 12345}))
    (root / "a" / "b" / "too-deep").mkdir(parents=True)
    (root / "a" / "b" / "too-deep" / "spec.md").write_text("# x\n")

    rows = {Path(b["dir"]).name: b for b in get(base, "/api/builds")}
    assert {k: (v["state"], v["group"], v["waiting"]) for k, v in rows.items()} == {
        "textstats": ("new", "needs_you", False),
        "asking": ("asking", "needs_you", True),
        "delivered": ("delivered", "needs_you", True),
        "running": ("running", "running", False),
        "accepted": ("accepted", "done", False),
        "rejected": ("rejected", "done", False),
        "failed": ("failed", "done", False),
        "stopped": ("stopped", "done", False),
    }
    assert rows["asking"]["open_questions"] == 1
    st = get(base, "/api/events", dir=rows["failed"]["dir"])["state"]
    assert st["error"] == "boom" and "log" in st
    assert len(scan(root)) == 8  # too-deep is three levels down


def test_running_build_shows_activity(ui):
    base, root = ui
    from repo_factory.events import EventLog

    d = root / "busy"
    d.mkdir()
    (d / "spec.md").write_text("# busy\n")
    log = EventLog(d / ".den" / "events.jsonl", "busy", 1)
    log.emit("build_started", pid=__import__("os").getpid())
    log.emit(
        "plan", version=1, milestones=[{"id": "m1", "title": "One"}, {"id": "m2", "title": "Two"}]
    )
    log.emit("milestone_started", id="m1", n=1)
    log.emit("candidate_started", candidate=1, milestone="m1", strategy="minimal")
    log.emit("call_started", call="c1", caller="scoper", unit="m1")
    log.emit("call_finished", call="c1", caller="scoper", usd=0.02, ok=True, wall_s=1)
    log.emit("call_started", call="c2", caller="miner", purpose="mine:edges", background=True)
    [row] = [b for b in get(base, "/api/builds") if b["id"] == "busy"]
    assert (row["state"], row["phase"], row["progress"], row["running_n"]) == (
        "running",
        "building",
        0.0,
        2,
    )
    r = get(base, "/api/events", dir=str(d.resolve()))
    assert r["next"] == 7 and not [e for e in r["events"] if e["type"].startswith("call_")]
    a = r["state"]["build"]["activity"]
    assert [x["label"] for x in a["running"]] == [
        "Worker: m1 · minimal",
        "Miner proposing edges rules · background",
    ]
    assert r["state"]["now"] >= a["running"][0]["started"]


def test_new_puts_a_typed_task_in_the_ledger(ui):
    base, root = ui
    text = "Add a list command to kv.py\n\nScript-friendly output, one key=value per line.\n"
    r = post(base, "/api/new", {"text": text})
    assert r["created"] and re.fullmatch(r"i[0-9a-f]{8}", r["id"]) and r["state"] == "new"
    again = post(base, "/api/new", {"text": text})  # the same task again: the same row
    assert again["id"] == r["id"] and not again["created"]
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, "/api/new", {"text": "  "})
    assert e.value.code == 400 and "describe" in json.loads(e.value.read())["error"]


def test_new_folder_creates_a_task_folder(ui, tmp_path):
    """A build started by hand (``repo-factory new``'s folder): seed, tests and budget."""
    import yaml

    from repo_factory.ui.api import App
    from repo_factory.ui.http import HttpError

    base, root = ui
    app = App(root, "fake", tmp_path / "home")
    seed = tmp_path / "mycode"
    (seed / ".git").mkdir(parents=True)
    (seed / "kv.py").write_text("STORE = {}\n")
    text = (
        "Add a list command to kv.py\n\nScript-friendly output, one key=value per line.\n\n"
        "- sorted by key\n- don't change the storage format\n"
    )
    r = app.new_folder(text, seed=str(seed))
    d = Path(r["dir"])
    assert d.parent == root.resolve() and (d / "task.yaml").exists()
    spec = (d / "spec.md").read_text()
    assert spec.startswith("# Add a list command to kv.py\n") and "- sorted by key\n" in spec
    assert (d / "repo" / "kv.py").exists() and not (d / "repo" / ".git").exists()
    assert yaml.safe_load((d / "task.yaml").read_text())["budget_usd"] is None
    assert any(b["dir"] == r["dir"] and b["state"] == "new" for b in get(base, "/api/builds"))
    again = app.new_folder(text, budget_usd="$12.5")  # same ask again: next free name
    assert Path(again["dir"]).name == d.name + "-2" and d.exists()
    assert yaml.safe_load((Path(again["dir"]) / "task.yaml").read_text())["budget_usd"] == 12.5
    for kw, msg in (
        (
            {"seed": str(tmp_path / "nope")},
            "Existing code: " + str(tmp_path / "nope") + " does not exist",
        ),
        ({"tests": str(seed / "kv.py")}, "is not a directory"),
        ({"seed": "relative/path"}, "absolute path"),
        ({"budget_usd": "lots"}, ""),
    ):
        with pytest.raises(HttpError) as e:
            app.new_folder("Other thing", **kw)
        assert e.value.code == 400 and msg in str(e.value)
    with pytest.raises(HttpError, match="describe"):
        app.new_folder("  ")
    assert not (root / "other-thing").exists()
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, "/api/verdict", {"dir": r["dir"], "verdict": "edit", "note": ""})
    assert e.value.code == 400


def test_cross_origin_post_refused(ui):
    base, root = ui
    req = urllib.request.Request(
        base + "/api/new",
        b'{"sentence": "x"}',
        {"Content-Type": "application/json", "Origin": "http://evil.example"},
    )
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 403


def test_binds_loopback_only(ui):
    from repo_factory.ui.server import HOST

    assert HOST == "127.0.0.1"


def test_busy_port_moves_to_next(tmp_path):
    # Another service may already hold the port: a busy port must not be fatal.
    blocker = make_server(tmp_path, port=0, harness="fake")
    busy = blocker.server_address[1]
    try:
        srv = make_server(tmp_path, port=busy, harness="fake", tries=5)
        assert srv.server_address[1] != busy
        srv.server_close()
        import pytest

        with pytest.raises(OSError):
            make_server(tmp_path, port=busy, harness="fake")  # tries=1: no fallback
    finally:
        blocker.server_close()


# -- the page's actions from the registry (AR-PLAN.md §7.4) ---------------------------------------
def old_build_acts(b: dict) -> set:
    """What the page's buttons were before the registry: app.js buildActs as it was (PR A),
    transcribed. New kinds (Ask for changes, Publish anyway) are not in it. Since a ready build
    waits for the owner's press, Publish (private) is offered on it, and the old Publish on a
    private repo is Make public."""
    out, idea = set(), b.get("idea")
    if b.get("idea_state") == "ready":
        out.add("publish")
    if b["status"] in ("ready", "kept") and idea:
        out |= {"make_public", "reject"}
    if b["status"] == "ready" and idea:
        out.add("keep")
    if (b["status"] == "held" and idea) or b["status"] == "failed":
        out.add("retry")
    if not idea and b["status"] in ("held", "ready") and b.get("state") == "delivered":
        out.add("reject")
    if not idea and b.get("state") in ("running", "asking"):
        out.add("stop")
    if (
        idea
        and b.get("idea_state") not in ("public", "closed")
        and b["status"] not in ("ready", "kept")
    ):
        out.add("close")
    return out


LOOP_STATUS = {
    "queued": "queued",
    "building": "running",
    "held": "held",
    "ready": "running",
    "published": "ready",
    "public": "public",
    "closed": "closed",
}
PARITY = [  # (idea state or None for a build by hand, build status, state, repo)
    *[
        (st, LOOP_STATUS[st], "new", None)
        for st in ("queued", "building", "held", "ready", "closed")
    ],
    ("published", "ready", "delivered", "o/p"),
    ("published", "kept", "delivered", "o/p"),
    ("public", "public", "delivered", "o/p"),
    (None, "queued", "new", None),
    (None, "running", "running", None),
    (None, "held", "asking", None),
    (None, "held", "delivered", None),
    (None, "ready", "delivered", None),
    (None, "failed", "failed", None),
    (None, "failed", "stopped", None),
    (None, "closed", "accepted", None),
]


@pytest.mark.parametrize("istate, status, state, repo", PARITY)
def test_the_registry_shows_what_the_old_page_showed(istate, status, state, repo):
    from repo_factory import registry as R

    row = {
        "key": "k",
        "dir": "/nowhere",
        "status": status,
        "state": state,
        "idea": "i12345678" if istate else None,
        "idea_state": istate,
        "repo": repo,
        "url": f"https://github.com/{repo}" if repo else None,
        "gate": {
            "verdict": "held",
            "reasons": ["x"],
            "checks": {"delivered": {"ok": False, "detail": "nothing delivered"}},
        },
        "question": {"id": "q1", "text": "?"} if state == "asking" else None,
    }
    shown = {
        a["kind"] for a in R.available(R.Subject("build", None, row)) if a["enabled"] and a["page"]
    } - {
        "revise",
        "publish_anyway",
        "stop_round",
        "resume_build",
        "cancel",
        "restart_revise",
    }  # newer kinds (AR, RC)
    assert shown == old_build_acts(row), (istate, status, state)


# -- the registry's doors: api/actions and api/act (AR-PLAN.md §5) -------------------------------
def test_api_actions_lists_every_action_with_why_not(tmp_path, monkeypatch):
    from .app_common import call, make_app

    lp, app, _ = make_app(tmp_path, monkeypatch)
    i, _ = lp.add("plumbers lose invoices", "ui")
    code, r = call(app, "GET", "/api/actions", {"kind": "idea", "id": i["id"]})
    assert code == 200 and r["subject"] == {"kind": "idea", "id": i["id"]}
    by = {a["kind"]: a for a in r["actions"]}
    assert by["send_to_build"]["enabled"] and by["later"]["enabled"]
    assert not by["retry"]["enabled"] and by["retry"]["why"] == "only a held build is retried"
    assert call(app, "GET", "/api/actions", {"kind": "idea", "id": "i00000000"})[0] == 404


def test_api_act_runs_through_the_loop_with_its_origin(tmp_path, monkeypatch):
    from .app_common import call, make_app

    lp, app, _ = make_app(tmp_path, monkeypatch)
    i, _ = lp.add("plumbers lose invoices", "ui")
    code, r = call(
        app,
        "POST",
        "/api/act",
        body={
            "kind": "later",
            "subject": {"kind": "idea", "id": i["id"]},
            "fields": {"days": 5},
            "origin": "cli",
        },
    )
    assert code == 200, r
    assert r["what"] == "put off for 5 days" and (lp.ledger.get(i["id"])["later_until"] or 0) > 0
    rows = lp.ledger.history(i["id"])
    verb = next(h for h in rows if h["note"].startswith("later:"))
    assert verb["data"]["origin"] == "cli"
    assert any(
        h["note"] == "cli: put off for 5 days" and h["data"]["origin"] == "cli" for h in rows
    )
    log = (lp.cfg.home / "actions.jsonl").read_text().splitlines()
    assert json.loads(log[-1])["origin"] == "cli"


def test_api_act_refuses_without_confirm_and_a_bad_origin(tmp_path, monkeypatch):
    from .app_common import call, make_app

    lp, app, _ = make_app(tmp_path, monkeypatch)
    i, _ = lp.add("plumbers lose invoices", "ui")
    body = {
        "kind": "close",
        "subject": {"kind": "idea", "id": i["id"]},
        "fields": {"reason": "not for me"},
        "origin": "cli",
    }
    code, r = call(app, "POST", "/api/act", body=body)
    assert code == 409 and r["error"] == "close needs confirm: true"
    assert lp.ledger.get(i["id"])["state"] == "new"
    for gone in ("chat", "mcp"):
        assert call(app, "POST", "/api/act", body={**body, "origin": gone})[0] == 400
    assert call(app, "POST", "/api/act", body={**body, "kind": "rm"})[0] == 400
    assert call(app, "POST", "/api/act", body={**body, "fields": {}, "confirm": True})[0] == 400
    code, r = call(app, "POST", "/api/act", body={**body, "origin": "ui", "confirm": True})
    assert code == 200 and lp.ledger.get(i["id"])["state"] == "closed"
