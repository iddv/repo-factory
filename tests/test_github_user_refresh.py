"""repo-factory's GitHub sign-in renews itself (SF item 1): an OAuth App with expiring tokens gives an
eight-hour token and a single-use refresh token. repo-factory keeps the refresh token at 0600, renews in
the token's last half hour under a lock, and says "click Connect again" on the page and in the
doctor when GitHub refuses. Against a fake GitHub on loopback; no token is printed anywhere."""

from __future__ import annotations

import json
import stat
import threading
import time

import pytest

from repo_factory import github_user as gu
from repo_factory.maintain import identity

from .fake_github_user import FakeGitHubUser

CFG = {"publish": {"gh_command": None}}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeGitHubUser()
    f.expiring = True
    monkeypatch.setenv("REPO_FACTORY_GITHUB_WEB", f.url)
    monkeypatch.setenv("REPO_FACTORY_GITHUB_API", f.url)
    monkeypatch.setenv("REPO_FACTORY_GITHUB_DIR", str(tmp_path / "repo-factory" / "github"))
    monkeypatch.delenv("REPO_FACTORY_GITHUB_CLIENT_ID", raising=False)
    yield f
    f.close()


def mode(p) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def signed_in(fake) -> gu.Store:
    store = gu.Store()
    gu.start("Ov23li.test", store)
    assert gu.poll(store)["state"] == "connected"
    return store


def test_the_sign_in_keeps_the_refresh_token_apart_at_0600(fake):
    store = signed_in(fake)
    assert mode(store.refresh_env) == 0o600 and mode(store.user_env) == 0o600
    assert store.refresh_env.read_text() == f"REFRESH_TOKEN={fake.refresh}\n"
    assert fake.refresh not in store.user_env.read_text()  # gh never sees it
    raw = store.user_json.read_text()
    assert fake.refresh not in raw and fake.token not in raw
    rec = store.record()
    assert rec["renews"] and 28700 < rec["expires_at"] - time.time() <= 28800
    st = gu.status(store)
    assert st["renews"] and st["expires_at"] and not st["needs_connect"]
    assert fake.refresh not in json.dumps(st)


def test_a_fresh_token_is_left_alone(fake):
    store = signed_in(fake)
    assert gu.refresh(store)["state"] == "fresh"
    assert fake.refreshes == []


def test_the_last_half_hour_renews_both_halves_and_the_old_pair_is_dead(fake):
    store = signed_in(fake)
    old_token, old_refresh = fake.token, fake.refresh
    soon = store.record()["expires_at"] - 600
    got = gu.refresh(store, now=soon)
    assert got["state"] == "refreshed"
    assert store.user_env.read_text() == f"GH_TOKEN={fake.token}\n" and fake.token != old_token
    assert store.refresh_token() == fake.refresh != old_refresh
    assert mode(store.user_env) == 0o600 and mode(store.refresh_env) == 0o600
    # the device flow's token renews with the client id alone: repo-factory has no client secret
    assert fake.refreshes == [{"client_id": "Ov23li.test", "grant_type": "refresh_token"}]
    rec = store.record()
    assert rec["refreshed"] and rec["refresh_error"] is None
    assert rec["expires_at"] - soon == pytest.approx(28800)
    assert gu.status(store, check=True)["valid"]


def test_two_processes_renew_once(fake):
    """Under user.lock the second one finds the token fresh: a single-use refresh token is
    never spent twice."""
    store = signed_in(fake)
    soon = store.record()["expires_at"] - 60
    out = []
    ts = [
        threading.Thread(target=lambda: out.append(gu.refresh(gu.Store(), now=soon)["state"]))
        for _ in range(4)
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == ["fresh", "fresh", "fresh", "refreshed"]
    assert len(fake.refreshes) == 1 and fake.refresh == store.refresh_token()


def test_a_refused_renewal_says_press_connect_again(fake):
    store = signed_in(fake)
    exp = store.record()["expires_at"]
    fake.answers = [{"error": "bad_refresh_token"}]
    got = gu.refresh(store, now=exp - 60)
    assert got["state"] == "failed" and "click Connect again" in got["line"]
    st = gu.status(store)
    assert "bad_refresh_token" in st["refresh_error"] and not st["needs_connect"]  # still valid
    fake.answers = [{"error": "bad_refresh_token"}]
    gu.refresh(store, now=exp + 1)
    rec = store.record()
    assert gu.needs_connect(rec, now=exp + 1).endswith(gu.AGAIN)
    assert fake.token not in json.dumps(rec) and fake.refresh not in json.dumps(rec)


def test_an_expired_unrenewed_token_is_not_used_and_the_gh_login_acts(fake, monkeypatch):
    store = signed_in(fake)
    exp = store.record()["expires_at"]
    fake.answers = [{"error": "bad_refresh_token"}]
    gu.refresh(store, now=exp - 60)
    monkeypatch.setattr(time, "time", lambda: exp + 5)
    fake.answers = [{"error": "bad_refresh_token"}]
    st = gu.status(store)
    assert st["needs_connect"] and "click Connect again" in st["line"] and not st["valid"]
    assert gu.connection(CFG).env_file is None and gu.connected_file() is None


def test_a_network_failure_is_kept_and_tried_again(fake):
    store = signed_in(fake)
    soon = store.record()["expires_at"] - 60
    fake.close()
    got = gu.refresh(store, now=soon)
    assert got["state"] == "failed" and got["kind"] == "network"
    assert "tries again" in gu.status(store)["refresh_error"]


def test_a_token_that_does_not_expire_is_never_renewed(fake):
    fake.expiring = False
    store = signed_in(fake)
    assert not store.refresh_env.exists()
    assert gu.refresh(store, now=10**11)["state"] == "none"
    assert gu.ensure_fresh(store)["state"] == "none" and fake.refreshes == []


def test_a_refused_token_is_marked_by_describe_and_connect_again_clears_it(fake):
    """The token on live today: from before renewal, refused once its eight hours ended. The
    maintenance identity read marks it; the page and the doctor then say Connect again; a new
    sign-in clears it."""
    store = signed_in(fake)
    g = gu.connection(CFG)
    g._run = lambda args, cwd=None: (1, "HTTP/2.0 401 Unauthorized\n\n{}", "")
    d = identity.describe(g, owner_gh=g)
    assert d["status"] == "fail" and "click Connect again" in d["line"]
    assert "maintenance token" not in d["line"]  # it is the sign-in, not a token file
    st = gu.status(store)
    assert st["needs_connect"] and "refused" in st["line"]
    assert gu.stale(g) and gu.connection(CFG).env_file is None
    gu.start("Ov23li.test", store)
    assert gu.poll(store)["state"] == "connected"
    assert not gu.status(store)["needs_connect"] and gu.connection(CFG).env_file == store.user_env


def test_install_shs_login_brings_its_refresh_token(fake, tmp_path):
    left = tmp_path / gu.INSTALL_LOGIN
    left.touch(mode=0o600)
    left.write_text(
        f"GH_TOKEN={fake.token}\nGH_REFRESH_TOKEN={fake.refresh}\n"
        "GH_TOKEN_EXPIRES_IN=28800\nGH_REFRESH_EXPIRES_IN=15897600\n"
    )
    assert gu.import_login(left, "Ov23li.test")["login"] == "ann" and not left.exists()
    store = gu.Store()
    assert store.user_env.read_text() == f"GH_TOKEN={fake.token}\n"
    assert store.refresh_token() == fake.refresh and store.record()["renews"]
    assert gu.refresh(store, force=True)["state"] == "refreshed"


def test_the_loops_tick_renews_in_the_background(fake, tmp_path):
    store = signed_in(fake)
    store.update(expires_at=time.time() + 120)  # in its last half hour
    old = fake.token
    assert gu.ensure_fresh()["state"] == "refreshed" and fake.token != old
    assert gu.ensure_fresh()["state"] == "fresh"
