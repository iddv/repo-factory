"""repo-factory's own GitHub App (GA-PLAN.md): the store, the manifest, the JWT the fake verifies,
tokens minted narrow and kept in memory, a refused token, the modes of every file, and no
secret in app.json or in any answer. The fake GitHub lives on loopback; the key is generated
when the fake starts."""

import json
import os
import re
import stat
import time

import pytest

from repo_factory import github_app as G
from repo_factory.gh import Gh

from .fake_github import FakeGitHub

pytestmark = pytest.mark.skipif(G.available() is not None, reason=G.available() or "")


@pytest.fixture
def gh_fake(tmp_path, monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setenv(G.API_ENV, fake.url)
    monkeypatch.setenv(G.DIR_ENV, str(tmp_path / "github"))
    G._APPS.clear()
    yield fake
    fake.close()
    G._APPS.clear()


def created(fake) -> G.Store:
    """An app made through the manifest exchange and installed on two repos."""
    store = G.Store()
    G.exchange(fake.code(), store)
    fake.install(["iddv/plotkeeper", "iddv/repo-factory"])
    G.app_for().refresh()
    return store


# -- the store ------------------------------------------------------------------------------
def test_the_store_dir_is_one_function(tmp_path):
    # in the one home (``--home``, else $REPO_FACTORY_HOME, else ~/.repo-factory)
    assert G.store_dir({}, home=tmp_path) == tmp_path / "github"
    assert G.store_dir({"REPO_FACTORY_HOME": str(tmp_path / "h")}) == tmp_path / "h" / "github"
    assert G.store_dir({G.DIR_ENV: str(tmp_path / "x")}) == tmp_path / "x"


def test_the_manifest_is_fixed_and_never_names_what_is_refused():
    m = G.manifest("127.0.0.1:8765", 8765, "iddv")
    assert m["name"] == "repo-factory-iddv" and m["public"] is False and m["default_events"] == []
    assert m["redirect_url"] == "http://127.0.0.1:8765/api/github/app/callback"
    assert m["setup_url"] == "http://127.0.0.1:8765/api/github/app/setup"
    assert (
        G.manifest("127.0.0.1:8765", 8765, "x", "/sub")["redirect_url"]
        == "http://127.0.0.1:8765/sub/api/github/app/callback"
    )
    assert m["default_permissions"] == {
        "metadata": "read",
        "issues": "write",
        "pull_requests": "write",
        "contents": "write",
        "checks": "read",
        "actions": "read",
    }
    assert "hook_attributes" not in m and "callback_urls" not in m
    assert "request_oauth_on_install" not in m
    text = json.dumps(m).lower()
    for bad in G.NEVER:
        assert f'"{bad}"' not in text, bad
    assert '"actions": "write"' not in text
    # the scopes narrow, never widen
    for name, perms in G.SCOPES.items():
        for k, v in perms.items():
            assert k in G.PERMISSIONS, (name, k)
            assert not (v == "write" and G.PERMISSIONS[k] != "write"), (name, k)
    assert G.SCOPES["issues"] == {"issues": "write", "metadata": "read"}
    assert G.SCOPES["push"] == {"contents": "write", "metadata": "read"}
    # the host must be loopback by name
    assert G.manifest("localhost:8790", 8790, "x")["redirect_url"].startswith(
        "http://localhost:8790/"
    )
    for bad in ("buildbox:8765", "192.168.1.2:8765", "", None, "repo-factory.local"):
        with pytest.raises(G.AppError, match="127.0.0.1"):
            G.manifest(bad, 8765, "x")
    assert G.post_url("abc") == "https://github.com/settings/apps/new?state=abc"
    assert (
        G.post_url("abc", "acme")
        == "https://github.com/organizations/acme/settings/apps/new?state=abc"
    )
    assert G.app_name("iddv") == "repo-factory-iddv" and G.app_name("") == "repo-factory"
    assert len(G.app_name("a" * 60)) <= 34


def test_the_pending_state_is_one_attempt_and_stale_after_ten_minutes(tmp_path):
    s = G.Store(tmp_path / "g")
    t0 = time.time()
    state = s.pending_write("127.0.0.1", 8765, now=t0)
    assert stat.S_IMODE((tmp_path / "g").stat().st_mode) == 0o700
    assert stat.S_IMODE(s.pending_path.stat().st_mode) == 0o600
    assert s.state() == "creating"
    assert s.pending_take("wrong", now=t0 + 1) is None  # wrong: used up all the same
    assert s.pending_take(state, now=t0 + 1) is None
    state = s.pending_write("127.0.0.1", 8765, now=t0)
    assert s.pending_take(state, now=t0 + G.PENDING_TTL_S + 1) is None  # stale
    state = s.pending_write("localhost", 8790, now=t0)
    got = s.pending_take(state, now=t0 + 2)
    assert got["host"] == "localhost" and got["port"] == 8790
    assert s.pending_take(state, now=t0 + 2) is None  # once
    assert s.pending_take(None) is None and s.state() == "none"


# -- the exchange, the key, the facts -------------------------------------------------------
def test_the_exchange_keeps_the_key_at_0600_and_no_secret_in_app_json(gh_fake):
    store = G.Store()
    rec = G.exchange(gh_fake.code(), store)
    assert rec["app_id"] == gh_fake.app_id and rec["slug"] == "factory-iddv"
    assert rec["bot_login"] == "factory-iddv[bot]" and rec["installation_id"] is None
    assert rec["key_fingerprint"].startswith("SHA256:")
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.app_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    text = store.app_path.read_text()
    assert "BEGIN" not in text and "client_secret" not in text and "fake-" not in text
    assert store.state() == "created"
    assert store.key_path.read_text() == gh_fake.pem
    # the code works once
    code = gh_fake.code()
    G.exchange(code, store)
    with pytest.raises(G.AppError, match="no longer knows that code"):
        G.exchange(code, store)
    with pytest.raises(G.AppError, match="does not look right"):
        G.exchange("bad code!", store)
    with pytest.raises(G.AppError):
        store.write_app({"app_id": 1, "pem": "x"})


def test_a_jwt_the_fake_verifies_and_a_token_minted_narrow(gh_fake):
    store = created(gh_fake)
    app = G.app_for()
    assert app.installed() and app.state() == "installed"
    rec = store.read()
    assert rec["installation_id"] == 777 and rec["repos"] == [
        "iddv/plotkeeper",
        "iddv/repo-factory",
    ]
    assert rec["bot_user_id"] == gh_fake.bot_user_id and app.bot_login == "factory-iddv[bot]"
    assert app.bot_email == f"{gh_fake.bot_user_id}+factory-iddv[bot]@users.noreply.github.com"
    assert gh_fake.jwt_errors == []
    # GET /app checks the key
    assert app.check()["id"] == gh_fake.app_id
    # a factory issue's token: issues write and metadata read, one repository
    gh_fake.token_requests.clear()
    tok = app.token("issues", ["iddv/repo-factory"])
    assert tok.startswith("ghs_") and len(tok) > 30
    assert gh_fake.token_requests == [
        {"permissions": {"issues": "write", "metadata": "read"}, "repositories": ["repo-factory"]}
    ]
    # the same scope and repo again: cached, no call
    assert app.token("issues", ["iddv/repo-factory"]) == tok and len(gh_fake.token_requests) == 1
    # another scope: another token
    push = app.token("push", ["iddv/plotkeeper"])
    assert push != tok and gh_fake.token_requests[-1] == {
        "permissions": {"contents": "write", "metadata": "read"},
        "repositories": ["plotkeeper"],
    }
    # a repo outside the installation is refused before any call
    n = len(gh_fake.requests)
    with pytest.raises(G.AppError, match="not installed on iddv/other"):
        app.token("issues", ["iddv/other"])
    assert len(gh_fake.requests) == n
    assert app.reaches("iddv/plotkeeper") and app.reaches("iddv/other") is False
    with pytest.raises(G.AppError, match="no such token scope"):
        app.token("admin")


def test_a_token_is_minted_again_under_five_minutes(gh_fake):
    created(gh_fake)
    clock = [time.time()]
    app = G.GitHubApp(G.Store(), now=lambda: clock[0])
    gh_fake.now = lambda: clock[0]
    a = app.token("maintain", ["iddv/plotkeeper"])
    clock[0] += 3600 - G.REFRESH_WITHIN_S - 10
    assert app.token("maintain", ["iddv/plotkeeper"]) == a  # still over five minutes
    clock[0] += 20
    b = app.token("maintain", ["iddv/plotkeeper"])
    assert b != a  # under: minted again
    # the JWT the mint sent was fresh for the fake's clock
    assert gh_fake.jwt_errors == []


def test_a_refused_token_says_so_and_never_falls_back(gh_fake):
    created(gh_fake)
    app = G.app_for()
    gh_fake.uninstall()
    app.forget()
    with pytest.raises(G.AppError, match="uninstalled, suspended, or its key") as e:
        app.token("maintain", ["iddv/plotkeeper"])
    assert e.value.kind in ("refused", "gone")
    # refresh finds no installation and records it
    with pytest.raises(G.AppError, match="not installed on any account"):
        app.refresh()
    assert app.installed() is False and app.state() == "created"
    assert "choose its repositories" in app.why_not()
    # the registration deleted on GitHub: GET /app is 404
    gh_fake.gone = True
    with pytest.raises(G.AppError, match="the app itself is gone"):
        app.check()


def test_gh_carries_the_token_in_one_environment_and_nothing_else(gh_fake, monkeypatch):
    created(gh_fake)
    app = G.app_for()
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "owner" * 5)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "other" * 5)
    gh = app.gh("issues", ["iddv/repo-factory"], cmd=["gh"])
    env = gh.env()
    tok = app.token("issues", ["iddv/repo-factory"])
    assert env["GH_TOKEN"] == tok and "GITHUB_TOKEN" not in env
    shaped = [v for v in env.values() if G.TOKEN_SHAPES.search(str(v))]
    assert shaped == [tok]
    assert gh.login() == "factory-iddv[bot]" and gh.credential == "app"
    assert "git-credential" in " ".join(gh.git_auth())
    assert "ghs_" not in " ".join(gh.git_auth())
    # nothing token-shaped in the Gh's own state
    assert tok not in repr(vars(gh)) and "ghs_" not in json.dumps(
        {k: str(v) for k, v in vars(gh).items() if k != "token"}
    )
    # a Gh whose app cannot act answers 4 with a clean line, as a broken token file does
    gh_fake.uninstall()
    app.forget()
    code, out, err = gh._subprocess(["api", "user"])
    assert code == 4 and "uninstalled" in err and "ghs_" not in err and "eyJ" not in err


def test_errors_are_clean_of_tokens_keys_and_jwts(gh_fake):
    store = created(gh_fake)
    tok = "ghs_" + "a1" * 20
    assert G.clean(f"x {tok} y") == "x [token] y"
    assert G.clean("ghs_" + "A" * 8 + "_" + "eyJ" + "b" * 40) == "[token]"
    assert G.clean("eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20) == "[jwt]"
    assert "[key]" in G.clean(store.key_path.read_text())
    assert "BEGIN" not in G.clean(store.key_path.read_text())
    e = G.AppError(f"bad {tok}")
    assert str(e) == "bad [token]"
    # the public view carries no secret and lists what is never granted
    v = G.public_view(store)
    text = json.dumps(v)
    assert "BEGIN" not in text and not re.search(r"ghs_[A-Za-z0-9]{20,}", text)
    assert v["state"] == "installed" and v["app"]["bot_login"] == "factory-iddv[bot]"
    assert v["app"]["has_key"] is True and "administration" in v["never"]
    assert v["app"]["install_url"].endswith("/installations/new")
    assert set(v["app"]) - set(G.APP_FIELDS) == {
        "install_url",
        "manage_url",
        "settings_url",
        "has_key",
    }


def test_replace_key_checks_with_github_first(gh_fake):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    store = created(gh_fake)
    app = G.app_for()
    old = store.key_path.read_text()
    other = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        .decode()
    )
    with pytest.raises(G.AppError, match="did not accept that key"):
        app.replace_key(other)
    assert store.key_path.read_text() == old  # untouched
    with pytest.raises(G.AppError, match="not a private key"):
        app.replace_key("hello")
    rec = app.replace_key(gh_fake.pem)  # the same key: accepted
    assert rec["key_fingerprint"] == G.fingerprint(gh_fake.pem)


def test_remove_uninstalls_and_empties_the_directory(gh_fake):
    store = created(gh_fake)
    app = G.app_for()
    out = app.remove()
    assert out["uninstalled"] is True and out["delete_url"].startswith("https://github.com/apps/")
    assert gh_fake.installation is None
    assert not store.app_path.exists() and not store.key_path.exists()
    assert store.state() == "none" and not app.exists()
    assert "no GitHub App yet" in app.why_not()


def test_a_key_readable_by_others_is_refused(gh_fake):
    store = created(gh_fake)
    app = G.app_for()
    app.forget()
    os.chmod(store.key_path, 0o644)
    with pytest.raises(G.AppError, match="chmod"):
        app.token("maintain")
    os.chmod(store.key_path, 0o600)
    assert app.token("maintain")


def test_the_job_record_is_clean(tmp_path):
    s = G.Store(tmp_path / "g")
    s.set_job("exchange", "running")
    assert s.state() == "creating"
    s.set_job("exchange", "failed", error="GitHub said ghs_" + "z" * 30)
    assert s.job()["error"] == "GitHub said [token]" and s.state() == "failed"
    s.set_job("refresh", "done")
    assert s.job()["what"] == "refresh" and "error" not in s.job()


def test_without_cryptography_the_app_is_unavailable_and_nothing_else_changes(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_crypto(name, *a, **k):
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("no cryptography")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_crypto)
    assert "repo-factory[github]" in G.available()
    assert "repo-factory[github]" in G.GitHubApp(G.Store("/nonexistent")).why_not()
    assert G.public_view(G.Store("/nonexistent"))["available"] is False


def test_the_plain_gh_is_unchanged():
    g = Gh(run=lambda a, c: (0, "iddv\n", ""))
    assert g.env() is None and g.git_auth() == [] and g.credential is None
    assert g.login() == "iddv"


# -- the key never reaches a sandbox (the review's rule 2) ------------------------------------
def test_no_session_sandbox_binds_the_config_dir_or_carries_a_token(tmp_path, monkeypatch):
    """Every session (a build's, a fix's, the chat's, an evolve run's) goes through
    ``harness.sandbox.command``: its mounts never reach ``~/.config/repo-factory`` (the key and the
    door tokens), and its environment carries no GitHub token and no path to the key."""
    from repo_factory.harness import isolation as iso
    from repo_factory.harness import sandbox

    home = tmp_path / "home" / "u"
    inst = home / ".local" / "share" / "claude" / "versions"
    inst.mkdir(parents=True)
    (inst / "9.9.9").write_text("")
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "claude").symlink_to(inst / "9.9.9")
    (home / ".local" / "bin" / "uv").write_text("")
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text("{}")
    key_dir = home / ".config" / "repo-factory" / "github"
    key_dir.mkdir(parents=True)
    (key_dir / "app.pem").write_text("not a key\n")
    (home / ".config" / "repo-factory" / "tokens").mkdir()
    ws = home / "factory-work" / "run" / "ws"
    ws.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GH_TOKEN", "ghs_" + "q" * 40)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "q" * 40)
    monkeypatch.setenv(G.DIR_ENV, str(key_dir))
    monkeypatch.setenv("REPO_FACTORY_TOKENS_DIR", str(home / ".config" / "repo-factory" / "tokens"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("REPO_FACTORY_WORKER_NET", raising=False)
    bins = {
        "bwrap": "/usr/bin/bwrap",
        "claude": str(home / ".local" / "bin" / "claude"),
        "uv": str(home / ".local" / "bin" / "uv"),
    }
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: bins.get(name))
    monkeypatch.setattr(iso, "isolation", lambda: "bwrap")
    cfg = home / ".config" / "repo-factory"
    for factory in ("package", "netproxy"):
        for m in sandbox.mounts(ws, tmp_path / "px", None, factory):
            assert not str(m["src"]).startswith(str(cfg)), m
            assert not str(m["dst"]).startswith(str(cfg)), m
        cmd = sandbox.command(ws, tmp_path / "px", None, factory)
        assert not any(str(cfg) in a for a in cmd), cmd
        assert not any("ghs_" in a or "ghp_" in a for a in cmd)
        assert "--setenv" in cmd and "GH_TOKEN" not in cmd and "GITHUB_TOKEN" not in cmd
        assert G.DIR_ENV not in cmd and "REPO_FACTORY_TOKENS_DIR" not in cmd
    env = sandbox.process_env()
    for k in ("GH_TOKEN", "GITHUB_TOKEN", G.DIR_ENV, "REPO_FACTORY_TOKENS_DIR"):
        assert k not in env, k
    assert not any("ghs_" in str(v) or "ghp_" in str(v) for v in env.values())
    for k in sandbox.PASS_ENV:
        assert not k.startswith(("GH_", "GITHUB_", "REPO_FACTORY_")), k
