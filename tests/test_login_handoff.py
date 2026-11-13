"""Every way repo-factory starts ``claude`` hands it the Claude token through a pipe and nowhere else
(TS.REVIEW R1 for builds, chat and the no-tools prompts; harness/handoff.py): neither the argv nor
the environment of the process holds it, and the pipe does. Runs anywhere (no bubblewrap): the
sandboxed profiles are checked at the process they would start, the others with a ``claude``
stand-in that records its start (``fake_claude_env.py``). Canaries are made at run time.

tests/test_login_sandbox.py is the same promise tested adversarially in real bubblewrap."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

from repo_factory.harness import claude_code, handoff, profile, sandbox, shell

from .term_common import canary

FAKE = Path(__file__).with_name("fake_claude_env.py")
OAUTH, KEY = "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"
OAUTH_FD, KEY_FD = handoff.TOKEN_ENV[OAUTH], handoff.TOKEN_ENV[KEY]


@pytest.fixture
def tokens(monkeypatch):
    """repo-factory's environment with a Claude token, an API key and a gateway token (canaries)."""
    c = {"oauth": canary("claude"), "key": canary("key"), "gateway": canary("gw")}
    monkeypatch.setenv(OAUTH, c["oauth"])
    monkeypatch.setenv(KEY, c["key"])
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", c["gateway"])
    return c


def nowhere(c: dict, *things) -> None:
    text = json.dumps(
        [list(map(str, t)) if isinstance(t, list) else t for t in things], default=str
    )
    for name, value in c.items():
        assert value not in text, f"the {name} canary is in the process's argv or environment"


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    d = tmp_path / "fakebin"
    d.mkdir()
    shim = d / "claude"
    shim.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    log = tmp_path / "started.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return lambda: [json.loads(x) for x in log.read_text().splitlines()]


@pytest.fixture
def boxed(tmp_path, monkeypatch, fake_claude):
    """The sandboxed profiles as if bubblewrap worked: the process they start is recorded
    (argv, environment, what the pipe holds) instead of run."""
    monkeypatch.setattr(sandbox, "available", lambda: True)
    seen = []

    def run(cmd, **kw):
        env = kw.get("env") or {}
        piped = {k: os.read(int(env[k]), 4096).decode() for k in handoff.TOKEN_FD_ENV if env.get(k)}
        seen.append({"argv": cmd, "env": env, "piped": piped, "fds": kw.get("pass_fds")})
        tools = (
            cmd[cmd.index("--tools") + 1].split(",")
            if "--tools" in cmd
            else list(profile.CORE_TOOLS)
        )
        kw["stdout"].write(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "s",
                    "tools": tools,
                    "mcp_servers": [],
                    "permissionMode": "dontAsk",
                }
            )
            + "\n"
        )
        kw["stdout"].write(
            json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0}
            )
            + "\n"
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        claude_code,
        "subprocess",
        types.SimpleNamespace(
            run=run,
            PIPE=subprocess.PIPE,
            TimeoutExpired=subprocess.TimeoutExpired,
            Popen=subprocess.Popen,
        ),
    )
    return seen


def setenv_of(argv: list) -> dict:
    """bwrap's ``--setenv NAME VALUE`` pairs."""
    return {argv[i + 1]: argv[i + 2] for i, a in enumerate(argv) if a == "--setenv"}


# -- the sandboxed launches: build stages, fixes and repairs (full), chat (read) ------------
@pytest.mark.parametrize("which", ["build stage", "maintain fix", "chat"])
def test_a_sandboxed_launch_gets_the_token_by_pipe_only(which, tmp_path, tokens, boxed):
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = tmp_path / "cfg"
    if which == "chat":
        profile.launch(
            "hi",
            ws,
            config_dir=cfg,
            max_turns=5,
            max_usd=None,
            timeout_s=30,
            tools="read",
            partial=True,
            transcript=tmp_path / "t.jsonl",
        )
    else:  # v5's stages and maintain's fixes: v5.launch
        from repo_factory.v5.launch import launch

        launch("build it", ws, config_dir=cfg, max_turns=5, max_usd=None, timeout_s=30)
    [s] = boxed
    nowhere(tokens, s["argv"], s["env"])
    assert s["piped"] == {OAUTH_FD: tokens["oauth"]}  # the OAuth token, by pipe
    assert s["env"][OAUTH_FD] == str(s["fds"][0]) and len(s["fds"]) == 1
    # every tool command through the shell prefix, bound read-only, one namespace per sandbox
    inside = setenv_of(s["argv"])
    assert inside["CLAUDE_CODE_SHELL_PREFIX"] == shell.INSIDE
    assert (inside["REPO_FACTORY_SHELL"], inside["REPO_FACTORY_SHELL_ENV"]) == ("session", "keep")
    a = s["argv"]
    [src] = [
        a[i + 1] for i in range(len(a) - 2) if a[i] == "--ro-bind" and a[i + 2] == shell.INSIDE
    ]
    assert os.access(src, os.X_OK)
    if which == "chat":
        rules = json.loads(s["argv"][s["argv"].index("--settings") + 1])["permissions"]["deny"]
        for d in ("/proc", "/sys", str(cfg)):
            assert f"Read(/{d}/**)" in rules


def test_an_api_key_goes_by_its_own_pipe(tmp_path, monkeypatch, boxed):
    key = canary("key")
    monkeypatch.delenv(OAUTH, raising=False)
    monkeypatch.setenv(KEY, key)
    # the unit's own key (repo-factory was started with it); the watcher's: test_build_login.py
    monkeypatch.setattr(handoff, "started_names", lambda: frozenset({KEY, "PATH", "HOME"}))
    ws = tmp_path / "ws"
    ws.mkdir()
    profile.launch("x", ws, config_dir=tmp_path / "cfg", max_turns=5, max_usd=None, timeout_s=30)
    [s] = boxed
    nowhere({"key": key}, s["argv"], s["env"])
    assert s["piped"] == {KEY_FD: key}


def test_without_a_token_nothing_is_piped_and_nothing_leaks(tmp_path, monkeypatch, boxed):
    gw = canary("gw")
    for k in (OAUTH, KEY):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", gw)
    ws = tmp_path / "ws"
    ws.mkdir()
    profile.launch("x", ws, config_dir=tmp_path / "cfg", max_turns=5, max_usd=None, timeout_s=30)
    [s] = boxed
    nowhere({"gw": gw}, s["argv"], s["env"])
    assert s["piped"] == {} and not s["fds"]
    assert not any(k in s["env"] for k in handoff.LOGIN_VARS)


def test_the_xai_key_reaches_no_process_factory_starts(tmp_path, tokens, monkeypatch, boxed):
    xai = canary("xai")
    monkeypatch.setenv("XAI_API_KEY", xai)
    ws = tmp_path / "ws"
    ws.mkdir()
    profile.launch("x", ws, config_dir=tmp_path / "cfg", max_turns=5, max_usd=None, timeout_s=30)
    [s] = boxed
    nowhere({"xai": xai}, s["argv"], s["env"])
    assert "XAI_API_KEY" not in handoff.process_env()
    assert "XAI_API_KEY" in handoff.LOGIN_VARS


@pytest.mark.parametrize("name", ["EXA_API_KEY", "BRAVE_API_KEY", "TAVILY_API_KEY"])
def test_a_search_key_reaches_no_process_factory_starts(tmp_path, tokens, monkeypatch, boxed, name):
    key = canary(name.split("_")[0].lower())
    monkeypatch.setenv(name, key)
    ws = tmp_path / "ws"
    ws.mkdir()
    profile.launch("x", ws, config_dir=tmp_path / "cfg", max_turns=5, max_usd=None, timeout_s=30)
    [s] = boxed
    nowhere({name: key}, s["argv"], s["env"])
    assert name not in handoff.process_env() and name in handoff.LOGIN_VARS
    shell = (Path(handoff.__file__).parent / "factory-shell").read_text()
    assert name in shell.split("line=$1")[0]


def test_the_sandbox_passes_no_login_variable():
    env = {
        OAUTH: "a",
        KEY: "b",
        "ANTHROPIC_AUTH_TOKEN": "c",
        "ANTHROPIC_BASE_URL": "u",
        "LANG": "C",
        "XAI_API_KEY": "d",
    }
    old = dict(os.environ)
    try:
        os.environ.update(env)
        got = sandbox.process_env()
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert got["ANTHROPIC_BASE_URL"] == "u" and got["LANG"] == "C"
    assert not any(k in got for k in handoff.LOGIN_VARS)


# -- the unsandboxed launches, with a real process ------------------------------------------
def check_started(rows, tokens) -> None:
    assert rows, "claude was not started"
    for r in rows:
        nowhere(tokens, r["argv"], r["env"])
        assert r["piped"] == {OAUTH_FD: tokens["oauth"]}


def test_chats_no_tools_fallback_and_a_stoppable_reply(tmp_path, tokens, fake_claude):
    ws = tmp_path / "ws"
    ws.mkdir()
    started = []
    profile.launch(
        "hi",
        ws,
        config_dir=tmp_path / "cfg",
        max_turns=5,
        max_usd=None,
        timeout_s=30,
        tools="none",
        partial=True,
        transcript=tmp_path / "t.jsonl",
        on_start=started.append,
    )
    assert started
    check_started(fake_claude(), tokens)


def test_the_restricted_profile(tmp_path, tokens, fake_claude):
    ws = tmp_path / "ws"
    ws.mkdir()
    claude_code.ClaudeCodeHarness(max_usd=None).run("x", ws, 30, tools="restricted")
    [r] = fake_claude()
    check_started([r], tokens)
    # its shell runs on the host: the prefix scrubs, no namespace
    assert (r["env"]["REPO_FACTORY_SHELL"], r["env"]["REPO_FACTORY_SHELL_ENV"]) == ("plain", "keep")
    assert os.access(r["env"]["CLAUDE_CODE_SHELL_PREFIX"], os.X_OK)


def test_the_no_tools_prompts(tmp_path, tokens, fake_claude):
    """The loop's pitch, Projects' evolve and charter (loop.models.claude), the rule set, the
    post-mortem and the pre-push honesty check (costs.ledger.call_claude), each attempt with
    a pipe of its own."""
    from repo_factory.costs.ledger import call_claude
    from repo_factory.loop.models import claude

    assert call_claude("x", caller="test")[0] == "ok"
    assert claude()("x")[0] == "ok"
    call_claude("x", caller="test", env={"CLAUDE_CONFIG_DIR": str(tmp_path / "c")})
    rows = fake_claude()
    assert len(rows) == 3 and rows[2]["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path / "c")
    check_started(rows, tokens)


# -- the shell prefix itself ----------------------------------------------------------------
def prefix(cmd: str, env: dict, kind: str) -> str:
    run_env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        **env,
        **shell.env(kind, shell.install()),
    }
    p = subprocess.run(
        [run_env["CLAUDE_CODE_SHELL_PREFIX"], cmd],
        env=run_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_the_prefix_drops_every_login_variable(tmp_path):
    c = {k: canary("v") for k in handoff.LOGIN_VARS}
    other = canary("other")
    keep = prefix("env", {**c, "SOME_CAPABILITY_SETTING": other}, "plain")
    strict = prefix("env", {**c, "SOME_CAPABILITY_SETTING": other}, "term-plain")
    for value in c.values():
        assert value not in keep and value not in strict
    assert other in keep and other not in strict  # builds keep their variables


def test_the_prefix_refuses_anything_but_one_command_line():
    p = subprocess.run([str(shell.install()), "a", "b"], capture_output=True, text=True)
    assert p.returncode == 2 and "one command line" in p.stderr


def test_the_installed_prefix_is_the_scripts_and_executable(tmp_path):
    a = shell.install()
    assert a.read_bytes() == shell.SCRIPT.read_bytes() and os.access(a, os.X_OK)
    a.chmod(0o644)
    assert os.access(shell.install(), os.X_OK)  # repaired
    dest = shell.install(tmp_path / "x" / "factory-shell")
    assert dest.is_file() and os.access(dest, os.X_OK)


# -- repo-factory doctor ---------------------------------------------------------------------------
def test_the_doctor_line(monkeypatch):
    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(handoff, "ptrace_scope", lambda: 1)
    assert handoff.doctor_line(True) == (
        "ok",
        "by pipe only, never in claude's environment or command line; tool commands run "
        "in a nested namespace without it (ptrace_scope 1)",
    )
    st, text = handoff.doctor_line(False)
    assert st == "warn" and "setup-token" in text and "login file" in text
    monkeypatch.setattr(sandbox, "available", lambda: False)
    st, text = handoff.doctor_line(True)
    assert st == "warn" and "no bubblewrap" in text and "by pipe only" in text
    monkeypatch.setattr(handoff, "ptrace_scope", lambda: 0)
    assert "ptrace_scope is 0" in handoff.doctor_line(True)[1]
