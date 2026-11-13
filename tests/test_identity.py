"""Who acts: the server decides from the request's token, never from its body.

``api.handle(..., identity=)`` is what ``repo-factory ui``'s server passes: a write
acts as that caller, a body that claims another origin is logged and ignored, and each door
runs only what ``registry.door_refuses`` allows. Every record says who it was, each round says
who started it, and Stop round and Cancel can be undone for a few seconds. Token
values are built at runtime by the store; none may reach a log or a record."""

import json
import logging
import os
import signal
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from repo_factory import registry as R
from repo_factory import tokens
from repo_factory.errors import LoopError
from repo_factory.loop import builds
from repo_factory.loop.daemon import Loop
from repo_factory.ui import api
from repo_factory.ui.server import make_server

from .loop_common import make_loop
from .test_build_controls import LONG, RED, alive, held, running, settle

UI = {"origin": "ui", "name": "ui", "actions": None}
MCP = {"origin": "mcp", "name": "mcp", "actions": None}
AGENT = {"origin": "agent:RC", "name": "RC", "actions": None}
ASK = "Fix the 4 failing own tests (see the gate); keep the tests' expectations."


def test_a_browser_extensions_token_does_not_act_here(tmp_path):
    """A ``browser:<name>`` token (a paired browser extension's) does not act here:
    repo-factory refuses it on every write, its actions included."""
    store = tokens.Store(tmp_path / "tok")
    name, value = store.add_browser("Chrome-laptop")
    assert name == "browser:chrome-laptop" and store.value(name) is None
    ident = store.identify(value)
    assert ident["origin"] == name
    for path in ("/api/act", "/api/loop/close", "/api/settings"):
        with pytest.raises(api.HttpError) as e:
            api.caller(ident, {"kind": "stop"}, path)
        assert e.value.code == 403, path


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_DIR", str(tmp_path / "gh"))
    monkeypatch.setenv("REPO_FACTORY_TOKENS_DIR", str(tmp_path / "tokens"))
    for k, v in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@t"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@t"),
    ):
        monkeypatch.setenv(k, v)
    yield
    for p in tmp_path.rglob("worker.pid"):  # never leave a fake session behind
        try:
            os.kill(int(p.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def act(app, identity, kind, iid, confirm=False, origin=None, **fields):
    body = {
        "kind": kind,
        "subject": {"kind": "idea", "id": iid},
        "fields": fields,
        "confirm": confirm,
        **({"origin": origin} if origin else {}),
    }
    code, _, data = api.handle(
        "POST", "/api/act", None, json.dumps(body).encode(), app=app, identity=identity
    )
    return code, json.loads(data)


def app_of(lp):
    return api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)


def notes(lp, iid, n=4):
    return [h["note"] for h in lp.ledger.history(iid)[:n]]


def actions_log(lp):
    f = lp.cfg.home / "actions.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


# -- the caller is the token ----------------------------------------------------------------
def test_a_spoofed_origin_acts_as_the_token_and_is_logged(tmp_path, caplog):
    lp, a, _ = held(tmp_path)
    lp.retry(a)  # queued: Cancel withdraws it
    app = app_of(lp)
    with caplog.at_level(logging.WARNING, logger="repo_factory.ui.api"):
        code, out = act(app, AGENT, "cancel", a, confirm=True, origin="ui")
    assert code == 200 and out["by"] == "agent:RC", out
    assert "the body says origin 'ui', the token is agent:RC" in caplog.text
    notes = [h["note"] for h in lp.ledger.history(a)[:3]]
    assert any("(agent:RC)" in n for n in notes) and not any("(ui)" in n for n in notes), notes
    h = next(h for h in lp.ledger.history(a) if h["data"].get("kind") == "cancel")
    assert h["data"]["origin"] == "agent:RC"
    assert actions_log(lp)[-1]["origin"] == "agent:RC"


def test_an_agent_token_cannot_run_what_an_agent_may_not(tmp_path):
    lp, a, _ = held(tmp_path)
    app = app_of(lp)
    code, out = act(app, AGENT, "stop", a, confirm=True, origin="ui")  # claims to be the page
    assert code == 403 and "agent:RC may not run stop" in out["error"]
    lp.retry(a)
    code, out = act(app, AGENT, "cancel", a)  # confirm class
    assert code == 409 and "needs confirm: true" in out["error"]
    # the page's own routes are the owner's: an agent token only runs actions
    for path, body in (
        ("/api/loop/close", {"id": a, "reason": "spoofed"}),
        ("/api/spend", {"daily_usd": 1}),
        ("/api/models", {"role": "x"}),
    ):
        code, _, data = api.handle(
            "POST", path, None, json.dumps({**body, "origin": "ui"}), app=app, identity=AGENT
        )
        assert code == 403 and "only runs actions" in json.loads(data)["error"], path
    # a door that left (the MCP server's) acts nowhere
    code, out = act(app, MCP, "cancel", a, confirm=True)
    assert code == 403 and "does not act here" in out["error"]
    assert lp.ledger.get(a)["state"] == "queued"
    code, out = act(app, UI, "cancel", a, confirm=True)
    assert code == 200 and out["by"] == "ui"


def test_an_agents_actions_are_its_tokens(tmp_path):
    lp, a, _ = held(tmp_path)
    app = app_of(lp)
    assert "stop" not in R.agent_default_actions() and "retry" in R.agent_default_actions()
    narrow = {**AGENT, "actions": ["retry"]}
    code, out = act(app, narrow, "revise", a, instructions=ASK)
    assert code == 403 and "agent:RC may not run revise" in out["error"]
    code, out = act(app, narrow, "retry", a)
    assert code == 200 and lp.ledger.get(a)["state"] == "queued"
    code, out = act(app, {"origin": "chat"}, "cancel", a, confirm=True)
    assert code == 403  # chat is in-process only, never a token


def test_an_agent_round_is_attributed(tmp_path):
    lp, a, ta = held(tmp_path)
    app = app_of(lp)
    code, _ = act(app, AGENT, "retry", a)
    assert code == 200 and lp.ledger.get(a)["build"]["by"]["origin"] == "agent:RC"
    (ta / "fake.json").write_text(json.dumps(RED))
    [s] = lp.start_builds()
    rec = s["build"]
    assert rec["started_by"]["origin"] == "agent:RC" and "by" not in rec
    h = lp.ledger.history(a)[0]
    assert h["note"].endswith("asked by agent:RC") and h["data"]["origin"] == "agent:RC"
    row = R.subject_of(app, "idea", a).row
    assert row["started_by"]["origin"] == "agent:RC"
    out = settle(lp, a)
    assert out["build"]["rounds_by"]["2"]["origin"] == "agent:RC"
    assert api.started_by(out) == {"origin": "agent:RC", "at": rec["started_by"]["at"], "round": 2}
    # round 1 came from the pitch: no one asked (the loop's own), and it says so
    assert (
        out["build"]["rounds_by"].get("1") is None
        or out["build"]["rounds_by"]["1"]["origin"] is None
    )


def test_an_agents_revise_round_starts(tmp_path, capsys):
    """The loop starts a revise round as ``repo-factory revise ... --origin agent:<name>``; repo-factory revise
    took only the five doors and exited before the round began (live, NoteHatch, v0.3.10)."""
    from repo_factory import cli

    with pytest.raises(SystemExit) as e:
        cli.main(
            [
                "revise",
                str(tmp_path / "no-such-task"),
                "--origin",
                "agent:EI",
                "--note-file",
                str(tmp_path / "note.md"),
            ]
        )
    err = capsys.readouterr().err
    assert "invalid" not in err and "--origin" not in err, err
    assert e.value.code != 2  # not argparse's usage error
    with pytest.raises(SystemExit) as e:
        cli.main(["revise", str(tmp_path), "--origin", "nobody"])
    assert e.value.code == 2 and "agent:<name>" in capsys.readouterr().err


def test_a_first_build_says_who_sent_it(tmp_path):
    lp = make_loop(tmp_path)
    lp.ledger.origin = "agent:RC"  # repo-factory loop send --origin=agent:RC
    idea, _ = lp.add("small clinics still schedule patients on paper")
    lp.send(idea["id"])
    lp.ledger.origin = None  # the daemon's own tick
    q = lp.pitch([idea["id"]])[0]
    assert q["pitch"]["by"]["origin"] == "agent:RC"
    (Path(q["task_dir"]) / "fake.json").write_text(json.dumps(RED))
    [s] = lp.start_builds()
    assert s["build"]["started_by"]["origin"] == "agent:RC"


# -- Undo -----------------------------------------------------------------------------------
def test_undo_stop_round_resumes_the_round(tmp_path):
    lp, a, ta = held(tmp_path)
    app = app_of(lp)
    lp.retry(a)
    running(lp, a, ta, LONG)
    code, out = act(app, UI, "stop_round", a, confirm=True)
    assert code == 200 and out["undo"]["of"] == "stop_round" and out["undo"]["until"] > time.time()
    assert settle(lp, a)["state"] == "held"
    row = R.subject_of(app, "idea", a).row
    assert (
        row["undo"]["of"] == "stop_round" and R.check("undo", R.subject_of(app, "idea", a)) is None
    )
    code, out = act(app, UI, "undo", a, of="stop_round")
    assert code == 200, out
    q = lp.ledger.get(a)
    assert q["state"] == "queued" and q["build"]["resume_round"] == 2
    assert not (ta / ".den" / "v5" / "r2" / builds.STOPPED).exists()
    assert any(n.startswith("undone by the owner (ui): round 2 continues") for n in notes(lp, a))
    code, out = act(app, UI, "undo", a)  # once
    assert code == 409


def test_undo_while_the_stop_is_under_way(tmp_path):
    lp, a, ta = held(tmp_path)
    lp.retry(a)
    pa, wa = running(lp, a, ta, {**LONG, "ignore_term": True})  # still there after SIGTERM
    lp.stop_round(a, origin="ui")
    assert lp.ledger.get(a)["state"] == "building"
    out = lp.undo(a, of="stop_round", origin="ui")
    assert out["build"]["kill"]["undo"]["origin"] == "ui"
    q = settle(lp, a, timeout=25)  # the reaper's SIGKILL, then the watch
    assert not alive(pa) and q["state"] == "queued" and q["build"]["resume_round"] == 2


def test_undo_a_stop_whose_round_was_not_saved_queues_it_again(tmp_path, monkeypatch):
    lp, a, ta = held(tmp_path)
    lp.retry(a)
    running(lp, a, ta, LONG)
    lp.stop_round(a, origin="ui")
    assert settle(lp, a)["state"] == "held"
    monkeypatch.setattr(builds, "resumable", lambda *a, **k: False)  # nothing saved to resume
    q = lp.undo(a, origin="ui")
    assert q["state"] == "queued" and q["build"]["resume_round"] is None
    assert not {"kill", "stopped"} & set(q["build"]) and q["build"]["by"]["origin"] == "ui"
    assert "its round was not saved, so it starts again" in lp.ledger.history(a)[0]["note"]


def test_undo_stop_round_by_an_agent(tmp_path):
    lp, a, ta = held(tmp_path)
    app = app_of(lp)
    lp.retry(a)
    running(lp, a, ta, LONG)
    code, out = act(app, AGENT, "stop_round", a, confirm=True)
    assert code == 200 and out["undo"]["of"] == "stop_round"
    assert settle(lp, a)["state"] == "held"
    code, out = act(app, AGENT, "undo", a, of="stop_round")
    assert code == 200, out
    q = lp.ledger.get(a)
    assert q["state"] == "queued" and q["build"]["resume_round"] == 2
    assert any("undone by the owner (agent:RC)" in n for n in notes(lp, a))


def test_undo_cancel_of_a_queued_revise_puts_it_back_exactly(tmp_path):
    lp, a, _ = held(tmp_path)
    app = app_of(lp)
    queued = lp.revise(a, ASK, origin="ui")
    code, out = act(app, UI, "cancel", a, confirm=True)
    assert code == 200 and out["undo"]["of"] == "cancel" and lp.ledger.get(a)["state"] == "held"
    code, out = act(app, UI, "undo", a, of="cancel")
    assert code == 200, out
    back = lp.ledger.get(a)
    for k in ("state", "reason", "gate", "publish", "pitch", "task_dir", "build"):
        assert back[k] == queued[k], k
    assert "Cancel undone by the owner (ui): queued again, as it was" in notes(lp, a)


def test_undo_cancel_of_a_new_build_brings_its_folder_back(tmp_path):
    lp = make_loop(tmp_path)
    idea, _ = lp.add("small clinics still schedule patients on paper")
    q = lp.pitch([idea["id"]])[0]
    task = Path(q["task_dir"])
    out = lp.cancel(idea["id"], origin="ui")
    assert out["state"] == "new" and not task.exists()
    back = lp.undo(idea["id"], origin="ui")
    assert back["state"] == "queued" and back["task_dir"] == str(task) and task.is_dir()
    assert back["pitch"] == q["pitch"]
    assert not list((Path(lp.cfg.tasks_dir) / ".cancelled").iterdir())


def test_undo_has_a_window_and_needs_the_same_state(tmp_path, monkeypatch):
    lp, a, _ = held(tmp_path, lp=make_loop(tmp_path, build={"kill_grace_s": 2, "undo_s": 0.01}))
    lp.retry(a)
    lp.cancel(a)
    monkeypatch.setattr(Loop, "UNDO_GRACE_S", 0)
    time.sleep(0.05)
    with pytest.raises(LoopError, match="too late"):
        lp.undo(a)
    lp2, b, _ = held(tmp_path / "two")
    lp2.retry(b)
    lp2.cancel(b)
    lp2.retry(b)  # changed since: the cancel stays
    with pytest.raises(LoopError, match="cannot be undone"):
        lp2.undo(b)


# -- the requests never wait, and say who they are -------------------------------------------
def test_a_request_never_waits_on_a_model(tmp_path, monkeypatch):
    lp = make_loop(tmp_path)
    assert lp.may_wait("cli") and not lp.may_wait("agent:RC") and not lp.may_wait("mcp")
    monkeypatch.setenv("REPO_FACTORY_NO_WAIT", "1")  # App.run sets it for every verb it runs
    assert not lp.may_wait("cli") and not lp.may_wait(None)


# -- repo-factory ui's server: the same guard, the cookie bootstrap -----------------------------------
def serve(tmp_path, lp):
    srv = make_server(lp.cfg.tasks_dir, port=0, harness="fake", loop_home=lp.cfg.home)
    import threading

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def call(url, body=None, headers=None):
    """A request as the page makes it: a write carries its own origin (the guard's rule)."""
    u = urllib.parse.urlparse(url)
    own = {"Origin": f"{u.scheme}://{u.netloc}"} if body is not None else {}
    req = urllib.request.Request(
        url,
        json.dumps(body).encode() if body is not None else None,
        {"Content-Type": "application/json", **own, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def test_factory_ui_refuses_writes_without_a_token_and_signs_a_browser_in(tmp_path):
    lp, a, _ = held(tmp_path)
    srv, base = serve(tmp_path, lp)
    store = srv.tokens
    try:
        code, hdr, _ = call(base + "/")
        assert code == 200 and "Set-Cookie" not in hdr  # the page hands out nothing
        code, _, data = call(base + "/api/loop/retry", {"id": a, "origin": "ui"})
        assert code == 401 and "not signed in" in json.loads(data)["error"]
        bogus = "x" + store.value("ui")[1:][::-1]
        code, _, _ = call(base + "/api/loop/retry", {"id": a}, {"Authorization": "Bearer " + bogus})
        assert code == 401
        # a cookie that carries another door's token is not a browser's
        name = tokens.cookie_name(srv.server_address[1])
        code, _, _ = call(
            base + "/api/loop/retry", {"id": a}, {"Cookie": f"{name}={store.value('cli')}"}
        )
        assert code == 401
        # the one-time link: the cookie, HttpOnly, SameSite=Strict, Path=/, this port's name
        code_ = store.mint_code()
        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(base + f"/auth/open?code={code_}&next=/%23/builds", timeout=30)
            raise AssertionError("expected a redirect")
        except urllib.error.HTTPError as e:
            assert e.code == 303 and e.headers["Location"] == "/#/builds"
            cookie = e.headers["Set-Cookie"]
        assert (
            cookie.startswith(f"{name}=")
            and "HttpOnly" in cookie
            and "SameSite=Strict" in cookie
            and "Path=/" in cookie
        )
        code, _, _ = call(base + f"/auth/open?code={code_}")  # used once
        assert code == 400
        value = cookie.split(";")[0].split("=", 1)[1]
        code, _, data = call(base + "/api/whoami", headers={"Cookie": f"{name}={value}"})
        assert json.loads(data)["origin"] == "ui"
        code, _, _ = call(base + "/api/loop/retry", {"id": a}, {"Cookie": f"{name}={value}"})
        assert code == 200 and lp.ledger.get(a)["state"] == "queued"
        # the sign-in box's code
        code, hdr, _ = call(base + "/auth/code", {"code": store.mint_code()})
        assert code == 200 and "HttpOnly" in hdr["Set-Cookie"]
        code, _, _ = call(base + "/auth/code", {"code": "not-a-code"})
        assert code == 400
        # an agent's token runs actions, as itself, whatever the body says; nothing else
        store.add("agent:RC")
        agent = {"Authorization": "Bearer " + store.value("agent:RC")}
        code, _, data = call(base + "/api/loop/close", {"id": a, "reason": "x"}, agent)
        assert code == 403
        code, _, data = call(
            base + "/api/act",
            {
                "kind": "cancel",
                "origin": "ui",
                "confirm": True,
                "subject": {"kind": "idea", "id": a},
            },
            agent,
        )
        assert code == 200 and json.loads(data)["by"] == "agent:RC"
    finally:
        srv.shutdown()


# -- the store and the logs ------------------------------------------------------------------
def test_the_store_keeps_hashes_and_private_files(tmp_path):
    s = tokens.Store(tmp_path / "t")
    assert s.ensure_doors() == ["ui", "cli"] and s.ensure_doors() == []
    assert stat.S_IMODE((tmp_path / "t").stat().st_mode) == 0o700
    index = (tmp_path / "t" / "index.json").read_text()
    for d in tokens.DOORS:
        v = s.value(d)
        assert v and v not in index and stat.S_IMODE(s.path(d).stat().st_mode) == 0o600
        assert s.identify(v)["origin"] == d
    s.add("agent:RC", ["retry"])
    v = s.value("agent:RC")
    assert s.identify(v) == {"origin": "agent:RC", "name": "RC", "actions": ["retry"]}
    s.rotate("agent:RC")
    assert s.identify(v) is None and s.identify(s.value("agent:RC"))["origin"] == "agent:RC"
    assert s.revoke("agent:RC") and s.value("agent:RC") is None
    with pytest.raises(tokens.TokenError):
        s.revoke("ui")
    with pytest.raises(tokens.TokenError):
        s.add("agent:no spaces")
    assert s.identify("") is None and s.identify(None) is None


def test_no_token_value_reaches_a_log_or_a_record(tmp_path, caplog):
    lp, a, _ = held(tmp_path)
    srv, base = serve(tmp_path, lp)
    store = srv.tokens
    store.add("agent:RC")
    values = [store.value(n) for n in ("ui", "cli", "agent:RC")]
    try:
        with caplog.at_level(logging.DEBUG):
            call(base + "/api/loop/retry", {"id": a})  # refused: no token
            call(base + "/api/loop/retry", {"id": a}, {"Authorization": "Bearer nope"})
            call(
                base + "/api/act",
                {"kind": "retry", "origin": "ui", "subject": {"kind": "idea", "id": a}},
                {"Authorization": "Bearer " + store.value("agent:RC")},
            )  # spoof: logged
            call(
                base + "/api/act",
                {"kind": "cancel", "confirm": True, "subject": {"kind": "idea", "id": a}},
                {"Authorization": "Bearer " + store.value("cli")},
            )
    finally:
        srv.shutdown()
    records = (
        caplog.text
        + (lp.cfg.home / "actions.jsonl").read_text()
        + json.dumps(lp.ledger.history(a))
        + json.dumps(lp.ledger.get(a))
    )
    assert "agent:RC" in records and "the body says origin" in caplog.text
    for v in values:
        assert v not in records
