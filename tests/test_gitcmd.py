"""The host's git on a tree a session or a stranger wrote (gitcmd): what that tree's .git asks
for never runs on the host. Everything here plants into temp dirs and only touches a marker."""

import re
import subprocess
from pathlib import Path

import pytest

from repo_factory import gitcmd
from repo_factory.v5 import repair

PKG = Path(__file__).resolve().parents[1] / "repo_factory"


def plant(ws: Path, marker_dir: Path) -> dict[str, Path]:
    """What a session inside bubblewrap can write: the workspace, .git included."""
    m = {k: marker_dir / k for k in ("hook", "fsmonitor", "clean", "process", "textconv")}
    hooks = ws / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for h in ("pre-commit", "post-commit", "reference-transaction", "post-index-change"):
        (hooks / h).write_text(f"#!/bin/sh\ntouch {m['hook']}\n")
        (hooks / h).chmod(0o755)
    cfg = {
        "core.fsmonitor": f"touch {m['fsmonitor']}; false",
        "filter.x.clean": f"sh -c 'touch {m['clean']}; cat'",
        "filter.Y.process": f"sh -c 'touch {m['process']}; exit 1'",
        "diff.t.textconv": f"sh -c 'touch {m['textconv']}; cat'",
    }
    for k, v in cfg.items():
        subprocess.run(["git", "config", k, v], cwd=ws, check=True)
    (ws / ".gitattributes").write_text("*.py filter=x\n*.txt filter=Y\n*.md diff=t\n")
    return m


def test_a_sessions_hooks_fsmonitor_and_filters_never_run_on_the_hosts_commit(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    repair.ensure_repo(ws, "base")  # the host makes the repo
    m = plant(ws, tmp_path)
    (ws / "product.py").write_text("print('hi')\n")
    (ws / "notes.txt").write_text("n\n")
    (ws / "README.md").write_text("# r\n")
    sha = repair.commit_all(ws, "fix(x): after a session")
    assert sha and repair.head(ws) == sha
    repair.git(ws, "diff", "HEAD~1", "HEAD")
    repair.git(ws, "show", "HEAD")
    repair.git(ws, "log", "-p", "-1")
    assert {k: p.exists() for k, p in m.items()} == {k: False for k in m}
    # the same tree with plain git runs them (the attack is real)
    (ws / "product.py").write_text("print('again')\n")
    r = subprocess.run(["git", "add", "product.py"], cwd=ws, capture_output=True, text=True)
    assert m["clean"].exists(), r.stderr


def test_an_ext_remote_never_runs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    gitcmd.git("init", "-q", cwd=ws)
    marker = tmp_path / "ext"
    subprocess.run(["git", "config", "protocol.ext.allow", "always"], cwd=ws, check=True)
    subprocess.run(
        ["git", "remote", "add", "evil", f"ext::sh -c touch% {marker}"], cwd=ws, check=True
    )
    p = gitcmd.run(["fetch", "-q", "evil"], cwd=ws, capture_output=True, text=True)
    assert p.returncode != 0 and not marker.exists()


def test_the_repos_own_commands_are_neutralised_and_the_owners_kept(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    owner, evil = tmp_path / "owner-helper-ran", tmp_path / "evil-helper-ran"
    subprocess.run(
        ["git", "config", "--global", "credential.helper", f"!touch {owner}; true"], check=True
    )
    ws = tmp_path / "ws"
    ws.mkdir()
    gitcmd.git("init", "-q", cwd=ws)
    for k, v in (
        ("credential.helper", f"!touch {evil}; true"),
        ("credential.https://github.com.helper", "evil-scoped"),
        ("core.sshCommand", "evil-ssh"),
        ("core.askPass", "evil-askpass"),
        ("merge.m.driver", "evil-merge"),
        ("remote.o.uploadpack", "evil-upload"),
    ):
        subprocess.run(["git", "config", "--add", k, v], cwd=ws, check=True)
    args = gitcmd.overrides(ws)
    pairs = [args[i + 1] for i in range(0, len(args), 2)]
    assert "credential.helper=" in pairs and f"credential.helper=!touch {owner}; true" in pairs
    assert "credential.https://github.com.helper=" in pairs
    assert "core.sshcommand=" in pairs and "core.askpass=" in pairs
    assert "merge.m.driver=" in pairs and "remote.o.uploadpack=git-upload-pack" in pairs
    assert not any("evil" in a for a in pairs)
    # what git ends up running when it needs a GitHub login: the owner's helper, not the tree's
    gitcmd.run(
        ["credential", "fill"],
        cwd=ws,
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
    )
    assert owner.exists() and not evil.exists()


@pytest.mark.parametrize("how", ["gitfile", "worktree", "insteadof"])
def test_a_tree_whose_git_would_act_outside_it_is_refused(tmp_path, how):
    ws = tmp_path / "ws"
    ws.mkdir()
    other = tmp_path / "owners-repo"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    if how == "gitfile":
        (ws / ".git").write_text(f"gitdir: {other / '.git'}\n")
    else:
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        k, v = (
            ("core.worktree", str(tmp_path))
            if how == "worktree"
            else (f"url.{other}.insteadOf", "https://github.com/")
        )
        subprocess.run(["git", "config", k, v], cwd=ws, check=True)
    (ws / "f").write_text("x")
    p = gitcmd.run(["add", "-A"], cwd=ws, capture_output=True, text=True)
    assert p.returncode == 128, p.stderr
    assert any(w in p.stderr for w in ("refuses", "moves the work tree", "rewrites")), p.stderr
    with pytest.raises(RuntimeError, match="refuses|moves the work tree|rewrites"):
        repair.git(ws, "add", "-A")
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=other, capture_output=True, text=True
        ).stdout
        == ""
    )


def test_git_never_walks_up_out_of_a_tree_unless_asked(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sub = tmp_path / "out"  # a delivery folder whose .git is gone
    sub.mkdir()
    assert gitcmd.run(["rev-parse", "--git-dir"], cwd=sub, capture_output=True).returncode != 0
    assert (
        gitcmd.run(
            ["rev-parse", "--git-dir"], cwd=sub, capture_output=True, discover=True
        ).returncode
        == 0
    )


def test_a_slow_git_times_out_as_a_failed_call(tmp_path):
    p = gitcmd.run(
        ["-c", "alias.slow=!sleep 5", "slow"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=0.5,
    )
    assert p.returncode == 124 and "timed out" in p.stderr
    with pytest.raises(subprocess.CalledProcessError):
        gitcmd.run(
            ["-c", "alias.slow=!sleep 5", "slow"],
            cwd=tmp_path,
            timeout=0.5,
            capture_output=True,
            check=True,
        )


def test_diff_show_and_log_never_use_an_external_diff_or_textconv():
    for sub in ("diff", "show", "log"):
        cmd = gitcmd.argv(sub, "HEAD")
        assert cmd[cmd.index(sub) + 1 : cmd.index(sub) + 3] == ["--no-ext-diff", "--no-textconv"]
    assert gitcmd.argv("-c", "x.y=z", "show", "HEAD")[-3:] == [
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
    ]


def test_no_module_runs_git_itself():
    """Every host git goes through gitcmd: a raw ``["git", ...]`` anywhere else is a hole."""
    raw = re.compile(r"""[\[(]\s*["']git["']\s*[,\])]|\*GIT\b""")
    hits = [
        f"{p.relative_to(PKG)}:{n}"
        for p in sorted(PKG.rglob("*.py"))
        if p.name != "gitcmd.py"
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if raw.search(line)
    ]
    assert hits == []


def test_the_hardening_works_without_a_cwd_and_in_a_fresh_dir(tmp_path):
    assert gitcmd.overrides(None) == [] and gitcmd.overrides(tmp_path / "missing") == []
    assert gitcmd.git("--version").startswith("git version")
    env = gitcmd.environ(tmp_path)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CEILING_DIRECTORIES"] == str(tmp_path.resolve().parent)


# -- a tree's own config never chooses where the host's git goes ------------------
def cloned(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin and the host's clone of it, its remotes pinned as the host wrote them."""
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    (src / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "one"],
        cwd=src,
        check=True,
    )
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(origin)], check=True)
    ws = tmp_path / "work" / "repo"
    ws.parent.mkdir()
    gitcmd.git("clone", "-q", str(origin), str(ws), cwd=ws.parent)
    gitcmd.pin(ws)
    return origin, ws


@pytest.mark.parametrize(
    "key,value",
    [
        ("http.proxy", "http://127.0.0.1:9"),
        ("http.sslVerify", "false"),
        ("http.https://github.com/.extraHeader", "X-Evil: 1"),
        ("http.cookieFile", "/etc/hostname"),
        ("remote.origin.proxy", "http://127.0.0.1:9"),
    ],
)
def test_a_trees_http_or_proxy_setting_is_refused_with_words(tmp_path, key, value):
    _, ws = cloned(tmp_path)
    subprocess.run(["git", "config", key, value], cwd=ws, check=True)
    for args in (["ls-remote", "--heads", "origin"], ["status", "--short"]):
        p = gitcmd.run(args, cwd=ws, capture_output=True, text=True)
        assert p.returncode == 128, p
        assert key.split(".")[0] in p.stderr and "credential" in p.stderr
    with pytest.raises(RuntimeError, match="refuses it"):
        gitcmd.git("fetch", "-q", "origin", cwd=ws)


@pytest.mark.parametrize("key", ["remote.origin.url", "remote.origin.pushurl", "remote.evil.url"])
def test_a_remote_the_host_did_not_write_is_refused_for_what_talks_to_it(tmp_path, key):
    origin, ws = cloned(tmp_path)
    evil = ws / ".repo-factory" / "e.git"  # what a session could make in its workspace
    evil.parent.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(evil)], check=True)
    marker = tmp_path / "RAN_ON_HOST"
    hook = evil / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    subprocess.run(["git", "config", key, str(evil)], cwd=ws, check=True)
    for args in (
        ["push", "-q", "origin", "HEAD:refs/heads/repo-factory/1-x"],
        ["ls-remote", "--heads", "origin"],
        ["fetch", "-q", "origin"],
    ):
        p = gitcmd.run(args, cwd=ws, capture_output=True, text=True)
        assert p.returncode == 128 and "did not write" in p.stderr, (args, p)
        assert key in p.stderr
    assert not marker.exists()
    # what never talks to a remote still works on the tree
    assert gitcmd.run(["status", "--short"], cwd=ws).returncode == 0


def test_a_tree_with_no_pin_is_never_pushed_from_and_a_pinned_one_is(tmp_path):
    origin, ws = cloned(tmp_path)
    gitcmd.pin_path(ws).unlink()
    p = gitcmd.run(
        ["push", "-q", "origin", "HEAD:refs/heads/x"], cwd=ws, capture_output=True, text=True
    )
    assert p.returncode == 128 and "did not write" in p.stderr
    gitcmd.pin(ws)
    assert gitcmd.pin_path(ws).parent == ws.parent  # beside the tree, never inside it
    gitcmd.git("push", "-q", "origin", "HEAD:refs/heads/x", cwd=ws)
    assert gitcmd.git("rev-parse", "x", cwd=origin).strip()
