"""Worker tool profiles (V4-C): ``restricted`` and ``full`` (the claude process in
bubblewrap, default tools, unrestricted shell), and the sandbox's network proxy. Offline:
bwrap and the claude/uv binaries are stand-ins, nothing is launched. Where the research
pipelines (v2-v4) use each profile: tests/lab/test_worker_profiles_v4.py."""

from __future__ import annotations

import pytest

from repo_factory.harness import claude_code as cc
from repo_factory.harness import isolation as iso
from repo_factory.harness import sandbox


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fake home with a claude install, uv and the Claude Code config; bwrap 'works'."""
    home = tmp_path / "home" / "u"
    inst = home / ".local" / "share" / "claude" / "versions"
    inst.mkdir(parents=True)
    (inst / "9.9.9").write_text("")
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "claude").symlink_to(inst / "9.9.9")
    (home / ".local" / "bin" / "uv").write_text("")
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text("{}")
    for d in ("workspace", "tasks", ".ssh", ".aws", "factory-work/other"):
        (home / d).mkdir(parents=True)
    ws = home / "factory-work" / "run" / "ws"
    ws.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("REPO_FACTORY_WORKER_UV_CACHE", raising=False)
    monkeypatch.delenv("REPO_FACTORY_WORKER_VENV_ROOT", raising=False)
    monkeypatch.delenv("REPO_FACTORY_WORKER_NET", raising=False)
    bins = {
        "bwrap": "/usr/bin/bwrap",
        "claude": str(home / ".local" / "bin" / "claude"),
        "uv": str(home / ".local" / "bin" / "uv"),
    }
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: bins.get(name))
    monkeypatch.setattr(iso, "isolation", lambda: "bwrap")
    return home, ws


def binds(cmd: list[str]) -> list[tuple[str, str, str]]:
    """(kind, src, dst) of every bwrap mount/link, in order."""
    out, i = [], 0
    while i < len(cmd):
        if cmd[i] in ("--bind", "--ro-bind", "--symlink"):
            out.append((cmd[i], cmd[i + 1], cmd[i + 2]))
            i += 3
        elif cmd[i] == "--tmpfs":
            out.append(("--tmpfs", "", cmd[i + 1]))
            i += 2
        else:
            i += 1
    return out


# -- the commands --------------------------------------------------------------------------


def test_restricted_command_is_unchanged(tmp_path):
    h = cc.ClaudeCodeHarness(model=None, max_turns=30, max_usd=1.0)
    root = f"/{tmp_path.resolve()}/**"
    expected = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--tools",
        "Read,Write,Edit,Bash",
        "--allowedTools",
        f"Read({root})",
        f"Edit({root})",
        f"Write({root})",
        "Bash(python check.py:*)",
        "Bash(python3 check.py:*)",
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--max-turns",
        "30",
        "--max-budget-usd",
        "1.00",
    ]
    assert h.command(tmp_path) == expected
    assert h.command(tmp_path, tools="restricted") == expected
    with pytest.raises(ValueError):
        h.command(tmp_path, tools="everything")


def test_the_machine_id_is_empty_inside(box, monkeypatch):
    home, ws = box
    cmd = cc.ClaudeCodeHarness(max_usd=1.0).command(
        ws, session={"id": "s1"}, tools="full", proxy_dir=home.parent / "px"
    )
    got = binds(cmd)
    there = [f for f in ("/etc/machine-id", "/etc/hostname") if sandbox.os.path.isfile(f)]
    if not there:
        pytest.skip("this machine has neither file")
    for f in there:
        at = got.index(("--ro-bind", "/dev/null", f))
        assert at > got.index(("--ro-bind", "/etc", "/etc"))  # over /etc, not under it


def test_full_command_wraps_claude_in_bwrap(box, monkeypatch):
    home, ws = box
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    h = cc.ClaudeCodeHarness(model=None, max_turns=250, max_usd=15.0)
    px = home.parent / "proxy-dir"
    cmd = h.command(ws, session={"id": "s1", "resume": None}, tools="full", proxy_dir=px)
    real_claude = str(home / ".local" / "share" / "claude" / "versions" / "9.9.9")
    i = cmd.index("--chdir") + 2  # bwrap's last option, then the interpreter
    box_args = cmd[:i]
    # own network namespace; the bridge to the host proxy execs claude
    assert cmd[i : i + 7] == [
        iso.base_python(),
        "-I",
        "/opt/repo_factory/src/repo_factory/harness/netproxy.py",
        "--bridge",
        "/run/repo_factory/proxy.sock",
        "3128",
        "--",
    ]
    assert cmd[i + 7] == real_claude
    args = cmd[i + 8 :]
    assert box_args[0] == "/usr/bin/bwrap" and "--share-net" not in box_args
    assert "--die-with-parent" in box_args and "--unshare-all" in box_args
    # claude's own arguments: default tool set, bare tools, dontAsk, no MCP, no bypass
    assert "--tools" not in args
    allowed = args[args.index("--allowedTools") + 1 : args.index("--permission-mode")]
    for t in (
        "Bash",
        "Read",
        "Edit",
        "Write",
        "Glob",
        "Grep",
        "TodoWrite",
        "Task",
        "WebFetch",
        "WebSearch",
        "NotebookEdit",
        "Skill",
    ):
        assert t in allowed
    assert all("(" not in t for t in allowed)
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in args and "--session-id" in args
    assert "--dangerously-skip-permissions" not in cmd and "bypassPermissions" not in cmd
    assert not any("skip-permissions" in a for a in cmd)
    # credentials and the parent's session never reach the command line, and the login never
    # reaches the sandbox's environment either (it goes by pipe: harness/handoff.py)
    assert "--clearenv" not in box_args and not any("sk-secret" in a for a in cmd)
    env = sandbox.process_env()
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_SESSION_ID" not in env
    # the mounts
    b = binds(box_args)
    hide = b.index(("--tmpfs", "", "/home"))
    assert ("--tmpfs", "", "/tmp") in b[:hide]
    under_home = [x for x in b if x[2].startswith("/home/") or x[2].startswith(str(home))]
    assert under_home and all(b.index(x) > hide for x in under_home)
    rw = {(s, d) for k, s, d in b if k == "--bind"}
    ro = {(s, d) for k, s, d in b if k == "--ro-bind"}
    assert rw == {
        (str(ws), str(ws)),
        (str(home / ".claude"), str(home / ".claude")),
        (str(home / ".claude.json"), str(home / ".claude.json")),
        (str(sandbox.worker_uv_cache()),) * 2,
        (str(sandbox.worker_venvs()),) * 2,
        (str(px), "/run/repo_factory"),
    }
    assert not px.exists()  # the proxy's dir is the proxy's to make
    # the workers' cache is in the home's cache folder (or an old install's ~/.cache one)
    from repo_factory import paths

    assert sandbox.worker_uv_cache() == paths.cache() / "worker-uv"
    inst = str(home / ".local" / "share" / "claude")
    assert (inst, inst) in ro and ("/etc", "/etc") in ro and ("/usr", "/usr") in ro
    assert (str(home / ".local" / "bin" / "uv"),) * 2 in ro
    # the factory is mounted away from its home directory
    assert (
        str(sandbox.factory_root() / "repo_factory"),
        "/opt/repo_factory/src/repo_factory",
    ) in ro
    for _, src, dst in b:
        for hidden in ("workspace", "tasks", ".ssh", ".aws", "factory-work/other"):
            assert not dst.startswith(str(home / hidden))
    assert not any(
        d in ("/run", "/var", "/srv", "/opt", "/mnt", "/media", "/")
        for _, _, d in b
        if _ != "--tmpfs"
    )
    assert not any(d.startswith("/run/systemd") for _, _, d in b)  # no host DNS needed
    assert box_args[box_args.index("--chdir") + 1] == str(ws)
    setenv = {box_args[j + 1]: box_args[j + 2] for j, a in enumerate(box_args) if a == "--setenv"}
    assert setenv["HOME"] == str(home) and setenv["PATH"].startswith("/opt/repo_factory/bin:")
    assert setenv["HTTPS_PROXY"] == setenv["http_proxy"] == "http://127.0.0.1:3128"
    assert "127.0.0.1" in setenv["NO_PROXY"]
    assert setenv["UV_CACHE_DIR"] == str(sandbox.worker_uv_cache())
    assert setenv["REPO_FACTORY_VENV_ROOT"] == str(sandbox.worker_venvs())
    assert setenv["DISABLE_AUTOUPDATER"] == "1"
    # every mount says why
    assert all(m["why"] for m in sandbox.mounts(ws))


def test_full_host_network_mode(box, monkeypatch):
    _, ws = box
    monkeypatch.setenv("REPO_FACTORY_WORKER_NET", "host")
    cmd = cc.ClaudeCodeHarness().command(ws, tools="full")
    i = cmd.index("--chdir") + 2
    assert "--share-net" in cmd[:i] and cmd[i] == str(sandbox.claude_binary())
    assert "netproxy.py" not in " ".join(cmd) and "/run/repo_factory" not in cmd
    assert "HTTPS_PROXY" not in cmd
    monkeypatch.setenv("REPO_FACTORY_WORKER_NET", "open")
    with pytest.raises(sandbox.SandboxUnavailable):
        cc.ClaudeCodeHarness().command(ws, tools="full")


def test_full_refuses_without_bwrap(box, monkeypatch):
    _, ws = box
    for level in ("netns", "none"):
        monkeypatch.setattr(iso, "isolation", lambda lv=level: lv)
        h = cc.ClaudeCodeHarness()
        with pytest.raises(sandbox.SandboxUnavailable):
            h.command(ws, tools="full")
        monkeypatch.setattr(
            cc.subprocess, "run", lambda *a, **k: pytest.fail("launched without a sandbox")
        )
        with pytest.raises(sandbox.SandboxUnavailable):
            h.run("hi", ws, 10, tools="full")
        assert h.command(ws)[0] == "claude"  # restricted keeps working without bwrap


def test_bwrap_cmd_defaults_unchanged(tmp_path):
    base = iso.bwrap_cmd("/usr/bin/bwrap", tmp_path)
    assert base[:5] == [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
    ]
    assert base == iso.bwrap_cmd("/usr/bin/bwrap", tmp_path, hide=(), extra=(), clearenv=True)
    x = iso.bwrap_cmd(
        "/usr/bin/bwrap", tmp_path, hide=("/home",), extra=("--bind", "/a", "/a"), clearenv=False
    )
    assert "--clearenv" not in x
    assert x.index("/home") - 1 == x.index("--tmpfs", x.index("/tmp"))
    assert x[x.index("--bind") : x.index("--bind") + 3] == ["--bind", "/a", "/a"]
    assert x.index("/a") < x.index(str(tmp_path))


# -- the network proxy ---------------------------------------------------------------------


def test_proxy_address_policy():
    from repo_factory.harness.netproxy import public

    for ip in (
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.10",
        "172.16.3.4",
        "169.254.169.254",
        "100.101.102.103",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "not-an-ip",
    ):
        assert not public(ip), ip
    for ip in ("1.1.1.1", "160.79.104.10", "2606:4700:4700::1111"):
        assert public(ip), ip


def _echo_server():
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            data = c.recv(1024)
            c.sendall(b"echo:" + data)
            c.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


def _via_proxy(sock_path, request: bytes) -> bytes:
    import socket

    s = socket.socket(socket.AF_UNIX)
    s.connect(str(sock_path))
    s.sendall(request)
    out = b""
    s.settimeout(5)
    try:
        while chunk := s.recv(4096):
            out += chunk
            if b"echo:" in out or b"Content-Length" in out and out.endswith(b"\n"):
                break
    except OSError:
        pass
    s.close()
    return out


def test_proxy_refuses_the_hosts_loopback_and_tunnels_what_it_allows():
    from repo_factory.harness.netproxy import Proxy

    srv, port = _echo_server()
    try:
        with Proxy() as px:  # the real policy
            r = _via_proxy(px.sock, f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode())
            assert r.startswith(b"HTTP/1.1 403")
            r = _via_proxy(
                px.sock,
                f"GET http://localhost:{port}/x HTTP/1.1\r\nHost: localhost\r\n\r\n".encode(),
            )
            assert r.startswith(b"HTTP/1.1 403")
            assert [v for v, _ in px.log] == ["denied", "denied"]
            d = px.dir
        assert not d.exists()
        with Proxy(allow=lambda ip: ip == "127.0.0.1") as px:  # a policy that allows it
            r = _via_proxy(px.sock, f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\nhello".encode())
            assert r.startswith(b"HTTP/1.1 200") and b"echo:hello" in r
    finally:
        srv.close()


def test_proxy_tries_each_address_of_a_name_ipv4_first(monkeypatch):
    """A name with an IPv6 address first, on a machine where that address does not answer
    (no IPv6 route, as on some CI runners): the proxy tries the next one."""
    from repo_factory.harness import netproxy

    srv, port = _echo_server()

    async def resolve(self, host, p):
        return sorted(["::1", "127.0.0.1"], key=lambda ip: ":" in ip)[::-1]  # v6 first

    monkeypatch.setattr(netproxy.Proxy, "_resolve", resolve)
    try:
        with netproxy.Proxy(allow=lambda ip: True) as px:
            r = _via_proxy(px.sock, f"CONNECT pkgs.example:{port} HTTP/1.1\r\n\r\nhi".encode())
            assert r.startswith(b"HTTP/1.1 200") and b"echo:hi" in r
    finally:
        srv.close()


def test_proxy_resolves_ipv4_first(monkeypatch):
    import asyncio
    import socket

    from repo_factory.harness import netproxy

    async def gai(self, host, port, type=0):
        return [
            (socket.AF_INET6, type, 6, "", ("2001:db8::1", port, 0, 0)),
            (socket.AF_INET, type, 6, "", ("192.0.2.1", port)),
        ]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", gai)
    px = netproxy.Proxy(allow=lambda ip: True)
    assert asyncio.run(px._resolve("pkgs.example", 443)) == ["192.0.2.1", "2001:db8::1"]


def test_bridge_forwards_a_local_port_to_the_proxy_and_execs(tmp_path):
    import socket
    import subprocess
    import sys

    from repo_factory.harness import netproxy

    srv, port = _echo_server()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    client = (
        "import socket; s = socket.create_connection(('127.0.0.1', %d)); "
        "s.sendall(b'CONNECT 127.0.0.1:%d HTTP/1.1\\r\\n\\r\\nping'); import time; "
        "time.sleep(0.5); print(s.recv(4096).decode())" % (free, port)
    )
    try:
        with netproxy.Proxy(allow=lambda ip: True) as px:
            p = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    netproxy.__file__,
                    "--bridge",
                    str(px.sock),
                    str(free),
                    "--",
                    sys.executable,
                    "-c",
                    client,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        assert p.returncode == 0, p.stderr
        assert "200 Connection Established" in p.stdout and "echo:ping" in p.stdout
    finally:
        srv.close()
