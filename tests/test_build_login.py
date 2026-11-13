"""BL: every Claude Code launch repo-factory makes follows one credential policy (harness/handoff.py
``choose``), and builds run on the person's Claude plan by default (the owner, 2026-10-18).

- the watcher's API key (only repo-factory's ``.env`` gave the process: not among the names it was
  started with, or named in ``$REPO_FACTORY_DOTENV_VARS``) reaches no build, fix or claude call, and a
  build is held before it starts with ``handoff.HOLD``;
- with ``build.allow_api_key`` it does;
- a Claude login token repo-factory keeps (``claude_login``) is used, ahead of everything else;
- a key in the unit's own environment still works;
- canaries in argv, environment, files and logs; the store is 0600 and never echoes the token.

No model, no network: claude is a stand-in that records its start (``fake_claude_env.py``),
the sandboxed launches are recorded instead of run, builds are ``builds.start`` recorded."""

from __future__ import annotations

import json
import logging
import os
import stat
import types

import pytest

from repo_factory import claude_login, registry
from repo_factory.harness import handoff, profile
from repo_factory.loop import builds
from repo_factory.settings.store import Config

from .loop_common import FakeModel, make_loop, pitch_json
from .term_common import canary
from .test_login_handoff import (  # noqa: F401
    KEY,
    KEY_FD,
    OAUTH,
    OAUTH_FD,
    boxed,
    fake_claude,
    nowhere,
)


@pytest.fixture(autouse=True)
def _own_loop_home(tmp_path, monkeypatch):
    """repo-factory's own loop.yaml (``allow``) is this test's alone."""
    monkeypatch.setenv("REPO_FACTORY_HOME", str(tmp_path / "repo-factory-loop"))


@pytest.fixture
def watchers_key(monkeypatch):
    """An environment with no login token and a Claude API key a ``.env`` loader added after
    the start (so not in ``/proc/self/environ``), named in ``$REPO_FACTORY_DOTENV_VARS``."""
    key = canary("key")
    monkeypatch.delenv(OAUTH, raising=False)
    monkeypatch.setenv(KEY, key)
    monkeypatch.setenv(handoff.DOTENV_ENV, KEY)
    return key


def allow(on: bool = True) -> None:
    """``build.allow_api_key`` in repo-factory's own loop.yaml (the one ``Config()`` finds)."""
    f = Config().layout.settings
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"build:\n  allow_api_key: {'true' if on else 'false'}\n")


def launch_all(tmp_path) -> None:
    """A build stage, a fix's session and a no-tools call: what every build, fix, revise and
    repair round and every pitch starts."""
    from repo_factory.costs.ledger import call_claude
    from repo_factory.v5.launch import launch

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    launch("build it", ws, config_dir=tmp_path / "cfg", max_turns=5, max_usd=None, timeout_s=30)
    profile.launch(
        "fix it", ws, config_dir=tmp_path / "cfg2", max_turns=5, max_usd=None, timeout_s=30
    )
    call_claude("x", caller="test", retries=1)


def no_file_holds(value: str, *roots) -> None:
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    assert value not in p.read_text(errors="replace"), f"{p} holds the canary"
                except OSError:
                    continue


def queued(lp, monkeypatch, n=1):
    """``n`` queued ideas on a loop whose builds are repo-factory's own (no build.command), with
    ``builds.start`` recorded instead of spawning anything."""
    lp.cfg.data["build"].update(command=None, revise_command=None)
    started = []
    monkeypatch.setattr(
        builds,
        "start",
        lambda cfg, task, *a, **k: (
            started.append(task) or {"pid": 999999, "started": 1.0, "resume": None, "cmd": []}
        ),
    )
    ids = []
    for k in range(n):
        lp._model = FakeModel(pitch_json(product=f"Product {k}"))
        idea, _ = lp.add(f"an idea for a build number {k} about thing {k * 7}")
        assert lp.pitch([idea["id"]])[0]["state"] == "queued"
        ids.append(idea["id"])
    return ids, started


# -- the watcher's key without the opt-in ----------------------------------------------------
def test_the_watchers_key_reaches_no_build_fix_or_call(
    tmp_path, watchers_key, boxed, fake_claude, caplog
):
    caplog.set_level(logging.DEBUG)
    launch_all(tmp_path)
    assert len(boxed) == 2
    for s in boxed:
        nowhere({"key": watchers_key}, s["argv"], s["env"])
        assert s["piped"] == {} and not s["fds"]
    [r] = fake_claude()
    nowhere({"key": watchers_key}, r["argv"], r["env"])
    assert r["piped"] == {}
    assert watchers_key not in caplog.text
    no_file_holds(watchers_key, tmp_path)
    assert handoff.source() is None
    assert handoff.choose()[1].watchers  # there, and held


def test_a_child_process_still_knows_the_watchers_key(monkeypatch):
    """A build repo-factory starts inherits the key and was started with it; the name in
    ``$REPO_FACTORY_DOTENV_VARS`` still says whose it is."""
    key = canary("key")
    env = {KEY: key, handoff.DOTENV_ENV: KEY, "PATH": "/bin"}
    usable, held = handoff.choose(env, started=set(env), allow=False)
    assert usable is None and held.watchers
    assert handoff.choose(env, started=set(env), allow=True)[0].value == key


def test_mark_dotenv_names_what_the_start_did_not_have():
    env = {KEY: "v", OAUTH: "w", "PATH": "/bin"}
    handoff.mark_dotenv(env, started={"PATH", OAUTH})
    assert env[handoff.DOTENV_ENV] == KEY
    handoff.mark_dotenv(env, started={"PATH", OAUTH})
    assert env[handoff.DOTENV_ENV] == KEY  # once


def test_a_build_is_held_before_it_starts_with_the_line(tmp_path, watchers_key, monkeypatch):
    lp = make_loop(tmp_path, concurrency=2)
    ids, started = queued(lp, monkeypatch, 2)
    assert lp.start_builds() == [] and started == []
    for i in ids:
        idea = lp.ledger.get(i)
        assert idea["state"] == "held" and idea["reason"] == handoff.HOLD
        assert idea["build"]["held_for"] == "login"
    assert handoff.HOLD == (
        "Builds need your Claude plan: set it up (claude setup-token), or allow the API key."
    )
    assert any(h["reason"] == handoff.HOLD for h in lp.status()["held"])  # Needs you
    # set up the plan: the held builds are queued again by themselves, and start
    claude_login.Store().keep(canary("plan"))
    out = lp.start_builds()
    assert len(out) == 2 and len(started) == 2
    assert all("held_for" not in lp.ledger.get(i)["build"] for i in ids)


def test_without_any_login_a_build_is_held_too(tmp_path, monkeypatch):
    for k in (OAUTH, KEY):
        monkeypatch.delenv(k, raising=False)
    lp = make_loop(tmp_path)
    ids, started = queued(lp, monkeypatch)
    assert lp.start_builds() == [] and not started
    assert lp.ledger.get(ids[0])["reason"] == handoff.HOLD


def test_claude_codes_own_sign_in_is_the_plan_too(tmp_path, watchers_key, monkeypatch):
    cfg_dir = tmp_path / "claude-home"
    cfg_dir.mkdir()
    (cfg_dir / ".credentials.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
    login = handoff.build_login()
    assert login["on"] == "signin" and login["held"]  # the key there, unused
    assert profile.auth_source() == "file"  # the sign-in is copied, no key
    lp = make_loop(tmp_path)
    ids, started = queued(lp, monkeypatch)
    assert len(lp.start_builds()) == 1


def test_a_hand_build_is_refused_before_it_starts(tmp_path, watchers_key):
    from repo_factory.ui.api import App, HttpError

    app = App(tmp_path / "tasks", "claude-code", tmp_path / "home")
    d = tmp_path / "tasks" / "t"
    (d / ".den").mkdir(parents=True)
    with pytest.raises(HttpError) as e:
        app.spawn(d, ["build", str(d)])
    assert e.value.code == 409 and str(e.value) == handoff.HOLD


# -- with the opt-in -------------------------------------------------------------------------
def test_with_the_opt_in_the_watchers_key_is_used(
    tmp_path, watchers_key, boxed, fake_claude, monkeypatch
):
    allow()
    launch_all(tmp_path)
    for s in boxed:
        nowhere({"key": watchers_key}, s["argv"], s["env"])
        assert s["piped"] == {KEY_FD: watchers_key}
    [r] = fake_claude()
    assert r["piped"] == {KEY_FD: watchers_key}
    login = handoff.build_login()
    assert login["on"] == "api_key" and login["words"] == "your Claude API key (opted in)"
    lp = make_loop(tmp_path, build={"allow_api_key": True})
    ids, started = queued(lp, monkeypatch)
    assert len(lp.start_builds()) == 1


# -- the login token repo-factory keeps -------------------------------------------------------------
def test_a_kept_login_token_is_used_ahead_of_the_watchers_key(
    tmp_path, watchers_key, boxed, fake_claude
):
    plan = canary("plan")
    claude_login.Store().keep(plan, how="cli")
    allow()  # even with the opt-in, the plan first
    launch_all(tmp_path)
    for s in boxed:
        nowhere({"key": watchers_key, "plan": plan}, s["argv"], s["env"])
        assert s["piped"] == {OAUTH_FD: plan}
    [r] = fake_claude()
    nowhere({"plan": plan}, r["argv"], r["env"])
    assert r["piped"] == {OAUTH_FD: plan}
    login = handoff.build_login()
    assert (login["on"], login["words"], login["stored"]) == (
        "login",
        "your Claude plan (login token)",
        True,
    )
    assert plan not in json.dumps(login) and plan not in repr(handoff.choose()[0])


def test_a_kept_token_that_others_can_read_is_not_used(tmp_path, monkeypatch):
    for k in (OAUTH, KEY):
        monkeypatch.delenv(k, raising=False)
    st = claude_login.Store()
    st.keep(canary("plan"))
    st.login_env.chmod(0o644)
    assert st.token() is None and st.status()["loose"]
    assert handoff.source() is None


# -- the unit's own key ----------------------------------------------------------------------
def test_a_key_in_the_units_environment_still_works(tmp_path, monkeypatch, boxed, fake_claude):
    key = canary("unitkey")
    monkeypatch.delenv(OAUTH, raising=False)
    monkeypatch.setenv(KEY, key)
    monkeypatch.setattr(handoff, "started_names", lambda: frozenset({KEY, "PATH", "HOME"}))
    launch_all(tmp_path)
    for s in boxed:
        nowhere({"key": key}, s["argv"], s["env"])
        assert s["piped"] == {KEY_FD: key}
    login = handoff.build_login()
    assert login["on"] == "api_key" and login["words"] == "your Claude API key (the unit's)"
    lp = make_loop(tmp_path)
    ids, started = queued(lp, monkeypatch)
    assert len(lp.start_builds()) == 1


def test_the_units_login_token_still_works(tmp_path, monkeypatch, boxed):
    tok = canary("unittok")
    monkeypatch.setenv(OAUTH, tok)
    monkeypatch.setenv(KEY, canary("key"))
    monkeypatch.setenv(handoff.DOTENV_ENV, KEY)
    ws = tmp_path / "ws"
    ws.mkdir()
    profile.launch("x", ws, config_dir=tmp_path / "cfg", max_turns=5, max_usd=None, timeout_s=30)
    assert boxed[0]["piped"] == {OAUTH_FD: tok}
    assert handoff.build_login()["words"] == "your Claude plan (login token)"


# -- the store and its setup -----------------------------------------------------------------
def test_the_store_keeps_the_token_at_0600_and_never_says_it(tmp_path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    plan = canary("plan")
    st = claude_login.Store()
    out = st.keep(plan, how="cli")
    assert stat.S_IMODE(st.login_env.stat().st_mode) == 0o600
    assert stat.S_IMODE(st.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(st.login_json.stat().st_mode) == 0o600
    assert st.token() == plan and out["stored"] and out["how"] == "cli"
    assert plan not in json.dumps(out) and plan not in st.login_json.read_text()
    assert plan not in capsys.readouterr().out and plan not in caplog.text
    assert st.forget() and st.token() is None and not st.forget()


@pytest.mark.parametrize("bad", ["", "two words", "a\nb", "short", "sk-ant-api" + "x" * 30])
def test_the_store_refuses_what_is_not_a_login_token(bad):
    with pytest.raises(claude_login.LoginError):
        claude_login.Store().keep(bad)


def test_the_store_reads_one_line_at_0600_in_the_home(tmp_path):
    """The token is kept in the one home (``<home>/claude/login.env``), one line, at 0600."""
    st = claude_login.Store(root=claude_login.store_dir({}, home=tmp_path))
    assert st.login_env == tmp_path / "claude" / "login.env"
    st.root.mkdir(parents=True, mode=0o700)
    plan = canary("plan")
    fd = os.open(st.login_env, os.O_WRONLY | os.O_CREAT, 0o600)
    os.write(fd, f"CLAUDE_CODE_OAUTH_TOKEN={plan}\n".encode())
    os.close(fd)
    assert st.token() == plan


def test_the_store_is_in_the_home(tmp_path):
    assert (
        claude_login.store_dir({"REPO_FACTORY_HOME": str(tmp_path / "h")})
        == tmp_path / "h" / "claude"
    )
    assert claude_login.store_dir({claude_login.DIR_ENV: str(tmp_path / "x")}) == tmp_path / "x"


def _ctx():
    lp = types.SimpleNamespace(
        ledger=types.SimpleNamespace(note=lambda *a, **k: None),
        cfg=types.SimpleNamespace(layout=types.SimpleNamespace(actions=os.devnull)),
    )
    return registry.Ctx(types.SimpleNamespace(loop=lambda: lp, loop_home=None), "ui")


def test_set_up_from_the_page_keeps_it_and_echoes_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    plan = canary("plan")
    out = registry.run(_ctx(), "claude_login_keep", {"token": plan}, registry.Subject("loop"))
    assert plan not in json.dumps(out) and plan not in caplog.text
    assert claude_login.Store().token() == plan
    with pytest.raises(registry.ActionError) as e:
        registry.run(_ctx(), "claude_login_forget", {}, registry.Subject("loop"))
    assert e.value.confirm
    registry.run(
        registry.Ctx(_ctx().app, "ui", confirm=True),
        "claude_login_forget",
        {},
        registry.Subject("loop"),
    )
    assert claude_login.Store().token() is None


def test_no_agent_or_mcp_sets_the_login():
    for origin in ("mcp", "agent:rc"):
        for kind in registry.CLAUDE_KINDS:
            assert registry.door_refuses(origin, kind, None, {}) is not None
    assert registry.door_refuses("ui", "claude_login_keep", None, {}) is None


def test_the_page_reads_what_builds_run_on_and_never_a_value(tmp_path, watchers_key):
    from repo_factory.ui.api import App
    from repo_factory.ui.routes.settings import _claude_login

    app = App(tmp_path / "tasks", "claude-code", tmp_path / "home")
    v = _claude_login(app, None)
    assert (v["on"], v["words"], v["line"]) == (None, "not set", handoff.HOLD)
    assert v["held"] and v["watchers_key"] and not v["allow_api_key"]
    assert v["command"] == "claude setup-token" and watchers_key not in json.dumps(v)
    plan = canary("plan")
    claude_login.Store().keep(plan)
    v = _claude_login(app, None)
    assert v["words"] == "your Claude plan (login token)" and v["kept_at"]
    assert plan not in json.dumps(v)
