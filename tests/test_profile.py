"""The one worker profile (build v5 §4.1): fingerprints, the comparison guard, clean config
dirs, the narrowed sandbox, launch-time assertions, and resume across config dirs. Offline:
nothing is launched (bwrap and claude are stand-ins)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repo_factory.harness import claude_code as cc
from repo_factory.harness import profile, sandbox
from repo_factory.harness.base import HarnessResult

from .test_worker_profiles import binds, box  # noqa: F401  (fixture)

TOOLS = ["Task", "Bash", "Edit", "Glob", "Grep", "Read", "Write", "WebFetch", "WebSearch"]


def init(**kw) -> dict:
    ev = {
        "type": "system",
        "subtype": "init",
        "session_id": "s",
        "tools": TOOLS,
        "mcp_servers": [],
        "model": "claude-opus-5-5[1m]",
        "permissionMode": "dontAsk",
        "claude_code_version": "2.1.288",
        "skills": ["debug"],
        "plugins": [],
        "agents": ["Explore"],
        "apiKeySource": "none",
    }
    ev.update(kw)
    return ev


def transcript(path: Path, *events, cost: float = 0.5) -> Path:
    lines = [json.dumps(e) for e in events]
    lines.append(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 3,
                "total_cost_usd": cost,
                "session_id": "s",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
    )
    path.write_text("\n".join(lines) + "\n")
    return path


# -- fingerprint -------------------------------------------------------------------------


def test_fingerprint_from_init_event(tmp_path):
    fp = profile.fingerprint(transcript(tmp_path / "t.jsonl", init()), net="proxy")
    assert fp["name"] == "full-v4" and fp["cli"] == "2.1.288"
    assert fp["model"] == "claude-opus-5-5[1m]" and fp["tools"] == sorted(TOOLS)
    assert fp["mcp_servers"] == [] and fp["permission_mode"] == "dontAsk"
    assert fp["net"] == "proxy" and fp["config"] == "clean"
    assert fp["hash"] == profile.profile_hash(fp) and len(fp["hash"]) == 64
    assert fp["info"]["skills"] == ["debug"]
    # tool order doesn't matter; info fields aren't hashed
    fp2 = profile.fingerprint(
        transcript(tmp_path / "u.jsonl", init(tools=list(reversed(TOOLS)), skills=["x"])),
        net="proxy",
    )
    assert fp2["hash"] == fp["hash"]
    # no init event: no hash
    miss = profile.fingerprint(transcript(tmp_path / "v.jsonl"))
    assert miss["hash"] is None and miss["missing"]
    assert profile.fingerprint(tmp_path / "absent.jsonl")["missing"]


def test_mcp_server_names_are_kept(tmp_path):
    fp = profile.fingerprint(
        transcript(
            tmp_path / "t.jsonl",
            init(mcp_servers=[{"name": "b", "status": "connected"}, {"name": "a", "status": "x"}]),
        )
    )
    assert fp["mcp_servers"] == ["a", "b"]
    assert "mcp_servers" in " ".join(profile.violations(fp))


# -- the comparison guard ----------------------------------------------------------------


@pytest.mark.parametrize(
    "field,change",
    [
        ("tools", {"tools": TOOLS[:-1]}),
        ("model", {"model": "claude-sonnet-5"}),
        ("cli", {"claude_code_version": "2.1.289"}),
        ("permission_mode", {"permissionMode": "default"}),
    ],
)
def test_compare_refuses_a_changed_profile(tmp_path, field, change):
    a = profile.fingerprint(transcript(tmp_path / "a.jsonl", init()), net="proxy")
    b = profile.fingerprint(transcript(tmp_path / "b.jsonl", init(**change)), net="proxy")
    profile.check_comparable({"a": a, "a2": dict(a)})
    with pytest.raises(profile.ProfileMismatch) as e:
        profile.check_comparable({"a": a, "b": b})
    assert e.value.field == field and field in str(e.value)
    assert profile.differing(a, b) == [field]


def test_compare_refuses_a_missing_fingerprint(tmp_path):
    a = profile.fingerprint(transcript(tmp_path / "a.jsonl", init()))
    for other in (None, {}, profile.fingerprint(transcript(tmp_path / "b.jsonl"))):
        with pytest.raises(profile.ProfileMismatch) as e:
            profile.check_comparable({"a": a, "b": other})
        assert e.value.field == "fingerprint"
    net = dict(a, net="host")
    net["hash"] = profile.profile_hash(net)
    assert profile.differing(a, net) == ["net"]


# -- clean config dirs -------------------------------------------------------------------


def owner_config(tmp_path: Path) -> Path:
    src = tmp_path / "owner" / ".claude"
    (src / "skills" / "mine").mkdir(parents=True)
    (src / "CLAUDE.md").write_text("owner instructions")
    (src / "settings.json").write_text("{}")
    (src / ".credentials.json").write_text('{"token": "t0"}')
    return src


def test_clean_config_holds_credentials_only(tmp_path):
    src = owner_config(tmp_path)
    d = tmp_path / "cfg"
    (d / "projects" / "-ws" / "memory").mkdir(parents=True)
    (d / "projects" / "-ws" / "s1.jsonl").write_text("{}\n")  # an earlier session
    for name in ("CLAUDE.md", "settings.json"):
        (d / name).write_text("x")
    (d / "skills" / "synced").mkdir(parents=True)
    (d / "plugins").mkdir()
    (d / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"x": {}},
                "projects": {"/ws": {"mcpServers": {"y": {}}, "allowedTools": []}},
                "numStartups": 3,
            }
        )
    )
    assert set(profile.is_clean(d)) >= {
        "CLAUDE.md",
        "settings.json",
        "skills",
        "plugins",
        ".claude.json:mcpServers",
    }
    profile.clean_config(d, src)
    assert profile.is_clean(d) == []
    assert (d / "projects" / "-ws" / "s1.jsonl").exists()  # sessions stay resumable
    assert (d / ".credentials.json").read_text() == '{"token": "t0"}'
    assert (d / ".credentials.json").stat().st_mode & 0o077 == 0
    state = json.loads((d / ".claude.json").read_text())
    assert "mcpServers" not in state and "mcpServers" not in state["projects"]["/ws"]
    assert state["numStartups"] == 3
    # the owner's dir is untouched
    assert (src / "CLAUDE.md").exists() and (src / "skills" / "mine").is_dir()


class Recorder:
    """A harness stand-in: records the call, writes a transcript, optionally 'refreshes'
    the credentials in the config dir it was given."""

    def __init__(self, ev: dict | None = None, refresh: str | None = None):
        self.ev = init() if ev is None else ev
        self.refresh = refresh
        self.calls: list[dict] = []

    def run(self, prompt, workspace, timeout_s, **opts):
        self.calls.append({"prompt": prompt, "workspace": workspace, **opts})
        cfg = Path(opts["config_dir"])
        cred = cfg / ".credentials.json"
        self.saw_credentials = cred.read_text() if cred.exists() else None
        if self.refresh:
            (cfg / ".credentials.json").write_text(self.refresh)
        t = Path(workspace) / "transcript.jsonl"
        transcript(t, *([self.ev] if self.ev else []))
        return HarnessResult(
            artifact_dir=workspace,
            tool_calls=1,
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.5,
            transcript_path=t,
            exit_ok=True,
            session_id=opts["session"]["id"],
            session_cost_usd=0.5,
        )


def test_launch_uses_the_profile_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.delenv("REPO_FACTORY_WORKER_NET", raising=False)
    src = owner_config(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    h = Recorder()
    r = profile.launch(
        "hi",
        ws,
        config_dir=tmp_path / "cfg",
        max_turns=5,
        max_usd=1.0,
        timeout_s=60,
        harness=h,
        source=src,
        transcript=tmp_path / "t.jsonl",
    )
    c = h.calls[0]
    assert c["tools"] == "full" and c["factory"] == "netproxy"
    assert c["config_dir"] == tmp_path / "cfg" and c["session"]["id"]
    assert c["max_turns"] == 5 and c["max_usd"] == 1.0
    assert h.saw_credentials == '{"token": "t0"}'
    assert not (tmp_path / "cfg" / ".credentials.json").exists()  # removed after the run
    assert r.transcript_path == tmp_path / "t.jsonl" and not (ws / "transcript.jsonl").exists()
    assert r.fingerprint["hash"] and r.fingerprint["name"] == "full-v4"


def test_a_sessions_credentials_never_go_back_to_the_owner(tmp_path, monkeypatch):
    """Decision O-3: what a session writes into its copy (a refresh, or anything) stays there."""
    monkeypatch.delenv("REPO_FACTORY_WORKER_NET", raising=False)
    src = owner_config(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    kw = dict(config_dir=tmp_path / "cfg", max_turns=5, max_usd=1.0, timeout_s=60, source=src)
    profile.launch("hi", ws, harness=Recorder(refresh='{"token": "t1"}'), **kw)
    assert (src / ".credentials.json").read_text() == '{"token": "t0"}'
    assert not (tmp_path / "cfg" / ".credentials.json").exists()  # the copy is gone

    class OwnerRefreshesToo(Recorder):
        def run(self, *a, **k):
            (src / ".credentials.json").write_text('{"token": "owner"}')
            return super().run(*a, **k)

    profile.launch("hi", ws, harness=OwnerRefreshesToo(refresh='{"token": "t2"}'), **kw)
    assert (src / ".credentials.json").read_text() == '{"token": "owner"}'  # never clobbered


def test_launch_assertions(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    kw = dict(
        config_dir=tmp_path / "cfg",
        max_turns=5,
        max_usd=1.0,
        timeout_s=60,
        source=tmp_path / "none",
    )
    monkeypatch.setenv("REPO_FACTORY_WORKER_NET", "host")
    h = Recorder()
    with pytest.raises(profile.ProfileError):
        profile.launch("hi", ws, harness=h, **kw)
    assert not h.calls  # refused before starting
    monkeypatch.delenv("REPO_FACTORY_WORKER_NET")
    with pytest.raises(profile.ProfileError, match="mcp_servers"):
        profile.launch("hi", ws, harness=Recorder(init(mcp_servers=[{"name": "x"}])), **kw)
    with pytest.raises(profile.ProfileError, match="tools missing"):
        profile.launch("hi", ws, harness=Recorder(init(tools=["Read", "Write"])), **kw)
    # a run that never started (no init event) is returned, without a fingerprint hash
    r = profile.launch("hi", ws, harness=Recorder(ev={}), **kw)
    assert r.fingerprint["missing"]


# -- the sandbox ---------------------------------------------------------------------------


def test_profile_mounts_hide_the_owners_config_and_the_package(box):  # noqa: F811
    home, ws = box
    cfg = home / "factory-bench" / "runs" / "t" / "p" / "s1" / "base" / "cfg"
    ms = sandbox.mounts(ws, config_dir=cfg, factory="netproxy")
    srcs = [m["src"] for m in ms]
    dsts = [m["dst"] for m in ms]
    pkg = str(sandbox.factory_root() / "repo_factory")
    assert str(home / ".claude") not in srcs + dsts
    assert str(home / ".claude.json") not in srcs + dsts
    assert pkg not in srcs and "/opt/repo_factory/src/repo_factory" not in dsts
    assert "/opt/repo_factory/venv" not in dsts
    opt_src = [m for m in ms if m["dst"].startswith("/opt/repo_factory/src")]
    assert [m["dst"] for m in opt_src] == ["/opt/repo_factory/src/repo_factory/harness/netproxy.py"]
    assert opt_src[0]["kind"] == "ro"
    assert {"kind": "rw", "src": str(cfg), "dst": str(cfg)}.items() <= next(
        m for m in ms if m["src"] == str(cfg)
    ).items()
    # the whole command: the bridge still runs, check.py's settings are gone
    cmd = profile.command(
        ws, config_dir=cfg, max_turns=10, max_usd=1.0, proxy_dir=home.parent / "px"
    )
    b = binds(cmd)
    assert (
        "--ro-bind",
        str(Path(sandbox.__file__).with_name("netproxy.py")),
        "/opt/repo_factory/src/repo_factory/harness/netproxy.py",
    ) in b
    assert not any(src == str(home / ".claude") or src == pkg for _, src, _ in b)
    assert "/opt/repo_factory/src/repo_factory/harness/netproxy.py" in cmd
    assert "REPO_FACTORY_CHECK_PYTHONPATH" not in cmd and "--model" not in cmd
    # legacy callers (v4) keep the whole package and ~/.claude
    legacy = sandbox.mounts(ws)
    assert any(m["src"] == pkg for m in legacy)
    assert any(m["src"] == str(home / ".claude") for m in legacy)
    with pytest.raises(ValueError):
        sandbox.mounts(ws, factory="everything")


def test_process_env_sets_the_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/elsewhere")
    assert sandbox.process_env()["CLAUDE_CONFIG_DIR"] == "/elsewhere"
    assert sandbox.process_env(tmp_path)["CLAUDE_CONFIG_DIR"] == str(tmp_path)


def test_profile_ignores_the_worker_model_env(box, monkeypatch):  # noqa: F811
    home, ws = box
    monkeypatch.setenv("REPO_FACTORY_WORKER_MODEL", "claude-sonnet-5")
    cmd = profile.command(ws, config_dir=home / "cfg", max_turns=10, max_usd=1.0)
    assert "--model" not in cmd
    cmd = profile.command(ws, config_dir=home / "cfg", max_turns=10, max_usd=1.0, model="claude-x")
    assert cmd[cmd.index("--model") + 1] == "claude-x"


def test_run_passes_config_dir_and_keeps_transcript_out(box, monkeypatch, tmp_path):  # noqa: F811
    home, ws = box
    seen = {}

    def fake_run(cmd, cwd, input, stdout, stderr, text, timeout, **kw):
        seen.update(cmd=cmd, env=kw.get("env"))
        stdout.write(json.dumps(init()) + "\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    cfg = home / "cfg"
    out = tmp_path / "logs" / "t.jsonl"
    r = cc.ClaudeCodeHarness(max_usd=1.0).run(
        "hi",
        ws,
        10,
        session={"id": "s1"},
        tools="full",
        config_dir=cfg,
        transcript=out,
        factory="netproxy",
    )
    assert r.transcript_path == out and out.exists() and not (ws / "transcript.jsonl").exists()
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(cfg)
    assert ("--bind", str(cfg), str(cfg)) in binds(seen["cmd"])
    # restricted runs get the config dir through the environment too
    cc.ClaudeCodeHarness(max_usd=1.0).run("hi", ws, 10, config_dir=cfg, transcript=out)
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(cfg)


# -- resume across config dirs -------------------------------------------------------------


def test_prepare_resume_across_config_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "global"))
    a, b = tmp_path / "cfg-a", tmp_path / "cfg-b"
    (a / "projects" / cc.project_key(tmp_path / "ws1")).mkdir(parents=True)
    (a / "projects" / cc.project_key(tmp_path / "ws1") / "s1.jsonl").write_text("{}\n")
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    assert cc.prepare_resume("s1", ws2, config_dir=b) == {"copied": [], "missing": ["s1"]}
    assert cc.prepare_resume("s1", ws2, config_dir=b, sources=[a]) == {
        "copied": ["s1"],
        "missing": [],
    }
    assert (b / "projects" / cc.project_key(ws2) / "s1.jsonl").exists()
    assert not (tmp_path / "global").exists()  # the global dir untouched
    # legacy: no config_dir means the process-global one
    g = tmp_path / "global" / "projects" / "-x"
    g.mkdir(parents=True)
    (g / "s2.jsonl").write_text("{}\n")
    assert cc.prepare_resume("s2", ws2)["copied"] == ["s2"]
    assert (tmp_path / "global" / "projects" / cc.project_key(ws2) / "s2.jsonl").exists()
