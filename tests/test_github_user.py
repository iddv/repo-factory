"""repo-factory's own GitHub connection (WZ-DESIGN §3, WZ-3): the device flow against a fake GitHub on
loopback, the store's modes, and the token's one place (the canary pattern of TS R1 and TL: a
token made at run time is found in its file and in the gh child's GH_TOKEN, nowhere else)."""

from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest

from repo_factory import github_user as gu
from repo_factory.gh import PublishError, token_env
from repo_factory.github_app import AppError, clean
from repo_factory.maintain import identity

from .fake_github_user import FakeGitHubUser

CFG = {"publish": {"gh_command": None}}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeGitHubUser()
    monkeypatch.setenv("REPO_FACTORY_GITHUB_WEB", f.url)
    monkeypatch.setenv("REPO_FACTORY_GITHUB_API", f.url)
    monkeypatch.setenv("REPO_FACTORY_GITHUB_DIR", str(tmp_path / "repo-factory" / "github"))
    monkeypatch.delenv("REPO_FACTORY_GITHUB_CLIENT_ID", raising=False)
    yield f
    f.close()


def mode(p) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_the_client_id_is_factory_oauth_app(fake):
    """repo-factory's OAuth App's client id is public (a client id is not a secret); a
    setting may replace it."""
    # no OAuth App is registered for repo-factory: the device flow is off unless an install sets one
    assert gu.CLIENT_ID == ""
    assert gu.client_id() == gu.CLIENT_ID and gu.client_id({"github": {}}) == gu.CLIENT_ID
    assert gu.client_id({"github": {"client_id": None}}) == gu.CLIENT_ID


def test_without_a_client_id_the_device_flow_is_not_offered(fake, monkeypatch):
    monkeypatch.setenv("REPO_FACTORY_GITHUB_CLIENT_ID", "")  # "" turns the device flow off
    assert gu.client_id() == "" and gu.client_id({"github": {"client_id": "x"}}) == ""
    monkeypatch.delenv("REPO_FACTORY_GITHUB_CLIENT_ID")
    assert gu.client_id({"github": {"client_id": ""}}) == ""
    monkeypatch.setenv("REPO_FACTORY_GITHUB_CLIENT_ID", "")
    with pytest.raises(AppError) as e:
        gu.start(gu.client_id())
    assert e.value.kind == "unconfigured" and "gh auth login" in str(e.value)
    assert gu.connection(CFG).env_file is None  # the login, exactly as before


def test_the_client_id_comes_from_the_settings_or_the_environment(fake, monkeypatch):
    assert gu.client_id({"github": {"client_id": "Ov23li.a"}}) == "Ov23li.a"
    monkeypatch.setenv("REPO_FACTORY_GITHUB_CLIENT_ID", "Ov23li.b")
    assert gu.client_id({"github": {"client_id": "Ov23li.a"}}) == "Ov23li.b"


def test_the_device_flow_connects_and_keeps_the_token_at_0600(fake):
    store = gu.Store()
    first = gu.start("Iv1.test", store)
    assert first["user_code"] == "ABCD-1234" and "device_code" not in first
    assert mode(store.pending_json) == 0o600 and mode(store.root) == 0o700
    assert mode(store.root.parent) == 0o700
    fake.answers = [{"error": "authorization_pending"}]
    assert gu.poll(store)["state"] == "pending"
    got = gu.poll(store)
    assert got["state"] == "connected" and got["login"] == "ann"
    assert sorted(got["scopes"]) == ["read:org", "repo", "workflow"]
    assert mode(store.user_env) == 0o600 and mode(store.user_json) == 0o600
    assert not store.pending_json.exists()
    assert store.user_env.read_text() == f"GH_TOKEN={fake.token}\n"
    rec = json.loads(store.user_json.read_text())
    assert fake.token not in json.dumps(rec) and rec["login"] == "ann"
    st = gu.status(store, check=True)
    assert st["connected"] and st["valid"] and st["line"].startswith("connected as ann")
    assert fake.token not in json.dumps(st)


def test_slow_down_adds_five_seconds_and_the_wait_respects_it(fake):
    fake.answers = [
        {"error": "authorization_pending"},
        {"error": "slow_down"},
        {"error": "authorization_pending"},
    ]
    slept, said, t = [], [], [1000.0]

    def sleep(s):
        slept.append(s)
        t[0] += s

    got = gu.wait("Iv1.test", gu.Store(), say=said.append, sleep=sleep, clock=lambda: t[0])
    assert got["state"] == "connected"
    assert slept == [5, 5, 10, 10]
    assert "ABCD-1234" in said[0] and "github.com/login/device" in said[0]


@pytest.mark.parametrize("err,state", [("expired_token", "expired"), ("access_denied", "denied")])
def test_an_ended_flow_keeps_nothing(fake, err, state):
    store = gu.Store()
    gu.start("Iv1.test", store)
    fake.answers = [{"error": err}]
    assert gu.poll(store)["state"] == state
    assert not store.pending_json.exists() and not store.user_env.exists()


def test_a_code_past_its_time_expires_without_asking_github(fake):
    store = gu.Store()
    gu.start("Iv1.test", store)
    n = len(fake.requests)
    assert gu.poll(store, now=10**11)["state"] == "expired"
    assert len(fake.requests) == n


def test_device_flow_off_on_the_app_says_so(fake):
    fake.device_error = "device_flow_disabled"
    with pytest.raises(AppError, match="Device Flow"):
        gu.start("Iv1.test")


@pytest.mark.parametrize("where", ["grant", "user"])
def test_wider_scopes_than_asked_are_refused_and_nothing_is_written(fake, where):
    if where == "grant":
        fake.granted_scope = "repo,workflow,read:org,delete_repo"
    else:
        fake.scopes = "repo, workflow, read:org, admin:org"
    store = gu.Store()
    gu.start("Iv1.test", store)
    with pytest.raises(AppError) as e:
        gu.poll(store)
    assert e.value.kind == "scopes" and fake.token not in str(e.value)
    assert not store.user_env.exists() and not store.user_json.exists()


def test_install_shs_login_is_kept_once_and_its_file_removed(fake, tmp_path):
    left = tmp_path / gu.INSTALL_LOGIN
    left.touch(mode=0o600)
    left.write_text(f"GH_TOKEN={fake.token}\n")
    got = gu.import_login(left, "Iv1.test")
    assert got["login"] == "ann" and not left.exists()
    assert gu.Store().connected()
    loose = tmp_path / "loose.env"
    loose.write_text(f"GH_TOKEN={fake.token}\n")
    loose.chmod(0o644)
    with pytest.raises(AppError, match="readable by other users"):
        gu.import_login(loose)
    assert not loose.exists()


def test_a_loose_token_file_is_not_used(fake):
    store = gu.Store()
    store.save(fake.token, "ann", ["repo"], "Iv1.test")
    store.user_env.chmod(0o644)
    assert not store.connected() and gu.connection(CFG).env_file is None
    st = gu.status(store)
    assert st["loose"] and "chmod 600" in st["line"]
    with pytest.raises(PublishError, match="chmod 600"):
        token_env(store.user_env)


def test_a_record_edited_to_hold_a_token_is_read_without_it(fake):
    store = gu.Store()
    store.save(fake.token, "ann", ["repo"], "Iv1.test")
    rec = json.loads(store.user_json.read_text())
    rec.update(token=fake.token, access_token=fake.token)
    store.user_json.write_text(json.dumps(rec))
    assert fake.token not in json.dumps(store.record())
    assert fake.token not in json.dumps(gu.status(store))


def test_disconnect_deletes_the_files_and_the_login_is_the_fallback_again(fake):
    store = gu.Store()
    store.save(fake.token, "ann", ["repo"], "Iv1.test")
    assert gu.connection(CFG).env_file == store.user_env
    assert store.disconnect() and not store.user_env.exists()
    assert gu.connection(CFG).env_file is None
    assert gu.status(store)["revoke_url"].startswith("https://github.com/settings/")


def test_the_canary_is_in_the_gh_childs_environment_and_nowhere_else(fake, monkeypatch):
    store = gu.Store()
    store.save(fake.token, "ann", ["repo", "workflow", "read:org"], "Iv1.test")
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw.get("env")))
        return subprocess.CompletedProcess(cmd, 1, "", "gh: something failed")

    monkeypatch.setattr(subprocess, "run", run)
    g = gu.connection(CFG)
    with pytest.raises(PublishError) as e:
        g("repo", "create", "ann/x", "--private")
    ((argv, env),) = seen
    assert fake.token not in " ".join(argv)
    assert env["GH_TOKEN"] == fake.token
    assert [k for k, v in env.items() if fake.token in v] == ["GH_TOKEN"]
    assert fake.token not in str(e.value)
    assert fake.token not in json.dumps(dict(os.environ))
    assert fake.token not in " ".join(g.git_auth())
    assert clean(f"oops {fake.token}") == "oops [token]"


def test_describe_names_the_connection(fake):
    gu.Store().save(fake.token, "ann", ["repo"], "Iv1.test")
    g = gu.connection(CFG)

    def run(args, cwd=None):
        return (
            0,
            ('HTTP/2.0 200 OK\nX-Oauth-Scopes: repo, workflow, read:org\n\n{"login": "ann"}'),
            "",
        )

    g._run = run
    d = identity.describe(g, owner_gh=g)
    assert d["kind"] == "connection" and d["login"] == "ann"
    assert d["line"].startswith("repo-factory's GitHub connection to ann (scopes repo, workflow")
