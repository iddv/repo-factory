"""The harness's read-only profiles (CHAT-PLAN.md section 4, now a project's evolve,
``evolve.tools``): the ``read`` tool profile (the build sandbox, Read/Glob/Grep only), the
``none`` fallback, a non-forking resume, partial messages, and a run the caller can stop."""

import json
import threading
import time
from pathlib import Path

import pytest

from repo_factory.harness import claude_code, profile, sandbox

from .app_common import install_fake_claude


def test_read_profile_flags():
    h = claude_code.ClaudeCodeHarness(max_turns=30, max_usd=None)
    cmd = h.command(Path("/w"), session={"id": "s1"}, tools="none", partial=True)
    assert cmd[0] == "claude" and cmd[cmd.index("--tools") + 1] == ""
    assert "--include-partial-messages" in cmd and "--allowedTools" not in cmd
    assert "--max-budget-usd" not in cmd and cmd[cmd.index("--max-turns") + 1] == "30"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk" and "--strict-mcp-config" in cmd
    # a linear resume: no fork, no new session id
    cmd = h.command(Path("/w"), session={"id": "s1", "resume": "s1", "fork": False}, tools="none")
    assert cmd[cmd.index("--resume") + 1] == "s1"
    assert "--fork-session" not in cmd and "--session-id" not in cmd
    # the build's own resume still forks
    cmd = h.command(Path("/w"), session={"id": "s2", "resume": "s1"}, tools="none")
    assert "--fork-session" in cmd and cmd[cmd.index("--session-id") + 1] == "s2"


@pytest.mark.skipif(not sandbox.available(), reason="needs bubblewrap")
def test_read_profile_is_sandboxed_with_read_tools_only(tmp_path):
    cmd = profile.command(
        tmp_path,
        config_dir=tmp_path / "cfg",
        max_turns=30,
        max_usd=None,
        tools="read",
        partial=True,
        proxy_dir=tmp_path / "px",
    )
    assert Path(cmd[0]).name == "bwrap" and "--die-with-parent" in cmd
    i = cmd.index("--tools")
    assert cmd[i + 1] == "Read,Glob,Grep"
    j = cmd.index("--allowedTools")
    assert cmd[j + 1 : j + 4] == ["Read", "Glob", "Grep"] and cmd[j + 4].startswith("--")
    for bad in ("Bash", "Write", "Edit", "WebFetch", "WebSearch"):
        assert bad not in cmd


def test_violations_for_the_read_profile():
    fp = {
        "tools": ["Read", "Glob", "Grep"],
        "mcp_servers": [],
        "net": "proxy",
        "permission_mode": "dontAsk",
    }
    assert profile.violations(fp, "read") == []
    assert profile.violations({**fp, "tools": []}, "none") == []
    assert profile.violations({**fp, "tools": ["Read", "Bash"]}, "read")
    assert profile.violations(fp, "full")  # the builders' core set is missing


def test_launch_none_streams_and_can_be_stopped(tmp_path, monkeypatch):
    log = install_fake_claude(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    tr = tmp_path / "t.jsonl"
    seen = {}
    r = profile.launch(
        "hello",
        ws,
        config_dir=tmp_path / "cfg",
        max_turns=5,
        max_usd=None,
        timeout_s=30,
        tools="none",
        partial=True,
        transcript=tr,
        on_start=lambda p: seen.setdefault("pid", p.pid),
    )
    assert r.exit_ok and seen["pid"] and r.cost_usd == pytest.approx(0.03)
    text = tr.read_text()
    assert "You said: hello" in text and "Let me look." in text and "text_delta" in text
    assert r.fingerprint["tools"] == []
    [c] = [json.loads(x) for x in log.read_text().splitlines()]
    assert "--include-partial-messages" in c["argv"]
    # stop: the process group gets SIGTERM; the run ends early
    holder = {}
    t0 = time.time()
    th = threading.Thread(
        target=lambda: holder.setdefault(
            "r",
            profile.launch(
                "SLOW",
                ws,
                config_dir=tmp_path / "cfg",
                max_turns=5,
                max_usd=None,
                timeout_s=60,
                tools="none",
                partial=True,
                transcript=tmp_path / "t2.jsonl",
                on_start=lambda p: holder.setdefault("p", p),
            ),
        )
    )
    th.start()
    while "p" not in holder:
        time.sleep(0.05)
    time.sleep(1)
    import os
    import signal

    os.killpg(holder["p"].pid, signal.SIGTERM)
    th.join(30)
    assert time.time() - t0 < 15 and not holder["r"].exit_ok
