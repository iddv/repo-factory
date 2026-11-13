"""A maintenance token GitHub refused (SF item 2): ``maintain.token_env_file``'s token answered
401 (expired or revoked). With another identity that works (repo-factory's GitHub sign-in here),
maintenance stops trying it and does not show it as the identity; the doctor names it once, as a
warning; Settings › Publishing › Maintenance offers Remove this token setting, which goes through
the one settings writer behind the house confirm. A fake ``gh``: no network, no model."""

import json
import secrets as pysecrets
import sys

import pytest
import yaml

from repo_factory import github_user as gu
from repo_factory.gh import Gh
from repo_factory.maintain import identity, poll
from repo_factory.ui import api

from .loop_common import make_loop

pytestmark = pytest.mark.usefixtures("maintenance_on")

# GitHub as gh sees it: the token in $DEAD is refused, any other answers as "ann"
GH = """import json, os, sys
a = sys.argv[1:]
tok = os.environ.get("GH_TOKEN") or ""
if a[:3] == ["api", "-i", "user"]:
    if tok and tok == os.environ.get("DEAD"):
        print("HTTP/2.0 401 Unauthorized"); print(); print("{}"); sys.exit(1)
    print("HTTP/2.0 200 OK"); print("X-OAuth-Scopes: repo, workflow, read:org"); print()
    print(json.dumps({"login": "ann"}))
elif a[:2] == ["api", "user"]:
    print("ann")
"""


def token(prefix="github_pat_"):
    return prefix + pysecrets.token_hex(20)  # built at runtime: never a literal


@pytest.fixture
def world(tmp_path, monkeypatch):
    script = tmp_path / "gh.py"
    script.write_text(GH)
    dead = token()
    monkeypatch.setenv("DEAD", dead)
    monkeypatch.setenv("REPO_FACTORY_GITHUB_DIR", str(tmp_path / "repo-factory" / "github"))
    monkeypatch.setenv("REPO_FACTORY_GITHUB_API", "http://127.0.0.1:9")  # never asked here
    f = tmp_path / "maintain.env"
    f.write_text(f"GH_TOKEN={dead}\n")
    f.chmod(0o600)
    home = tmp_path / "loop"
    home.mkdir()
    (home / "loop.yaml").write_text(
        yaml.safe_dump(
            {
                "publish": {"gh_command": [sys.executable, str(script)]},
                "maintain": {"token_env_file": str(f)},
            }
        )
    )
    return home, f, dead


def sign_in():
    gu.Store().save(token("gho_"), "ann", ["read:org", "repo", "workflow"], "Ov23li.test")


def test_a_refused_token_is_noted_and_lapses_when_the_file_changes(world):
    home, f, dead = world
    from repo_factory.settings.store import Config

    cfg = Config(home)
    d = identity.describe(identity.maintain_gh(cfg), home=home)
    assert d["status"] == "fail" and d["refused"] and "maintain.token_env_file" in d["line"]
    assert identity.token_refused(cfg) == str(f) and dead not in json.dumps(d)
    assert dead not in identity._refused_path(home).read_text()
    f.write_text(f"GH_TOKEN={token()}\n")  # a new token in the same file
    import os

    os.utime(f, (1, 1))
    assert identity.token_refused(cfg) is None


def test_with_the_sign_in_working_maintenance_stops_using_the_refused_token(world):
    home, f, _ = world
    from repo_factory.settings.store import Config

    cfg = Config(home)
    identity.describe(identity.maintain_gh(cfg), home=home)
    assert identity.maintain_gh(cfg).env_file == f  # nothing else works: still tried
    assert identity.token_skipped(cfg) is None
    sign_in()
    assert identity.token_skipped(cfg) == str(f)
    assert identity.maintain_gh(cfg) is None and identity.resolve(cfg) is None
    lp = make_loop(home.parent / "lp")
    lp.cfg.data["maintain"]["token_env_file"] = str(f)
    identity.note_refused(lp.cfg.home, f)
    assert poll.gh_for(lp) is lp.gh  # the sign-in, not the dead token
    cfg.data["maintain"]["identity"] = "token"  # the owner chose the token: kept
    assert identity.maintain_gh(cfg).env_file == f


def test_the_doctor_names_the_refused_token_once_as_a_warning(world):
    home, f, _ = world
    sign_in()
    status, line = identity.doctor(home)
    assert status == "warn" and line.startswith("repo-factory's GitHub connection to ann")
    assert line.count(str(f)) == 1 and "is not used: remove the setting" in line
    assert "the maintenance token" not in line  # not shown as the identity
    status, line = identity.doctor(home)  # the next run: the same, once
    assert status == "warn" and line.count(str(f)) == 1


def test_without_another_identity_the_refusal_is_the_doctors_fail(world):
    home, f, _ = world
    status, line = identity.doctor(home)
    assert status == "fail" and "was refused by GitHub" in line and line.count(str(f)) == 1


def test_the_page_offers_remove_this_token_setting_through_the_one_writer(world):
    home, f, _ = world
    sign_in()
    lp = make_loop(home.parent / "lp")
    lp.cfg.data["publish"]["gh_command"] = yaml.safe_load((home / "loop.yaml").read_text())[
        "publish"
    ]["gh_command"]
    lp.cfg.data["maintain"]["token_env_file"] = str(f)
    lp.cfg.layout.settings.write_text(
        yaml.safe_dump({"maintain": {"token_env_file": str(f), "poll_s": 600}})
    )
    identity.note_refused(lp.cfg.home, f)
    a = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a.loop, a.loop_cfg = (lambda: lp), (lambda: lp.cfg)
    code, _, body = api.handle("GET", "/api/maintain/summary", None, None, app=a)
    i = json.loads(body)["identity"]
    assert code == 200 and i["refused_token"] == str(f) and i["token_skipped"]
    assert i["kind"] == "connection" and i["login"] == "ann"
    assert "stays where it is" in i["remove_confirm"]

    def post(b):
        return api.handle("POST", "/api/loop/settings", None, json.dumps(b).encode(), app=a)

    assert post({"key": "maintain.token_env_file", "clear": True})[0] == 409  # the dialog first
    assert (
        post({"key": "maintain.token_env_file", "value": "/tmp/x.env", "confirm": True})[0] == 400
    )
    code, _, _ = post({"key": "maintain.token_env_file", "clear": True, "confirm": True})
    assert code == 200
    kept = yaml.safe_load(lp.cfg.layout.settings.read_text())
    assert kept == {"maintain": {"poll_s": 600}} and f.exists()  # the file itself stays


def test_a_gh_made_from_the_refused_token_reads_it_only_in_the_child(world):
    home, f, dead = world
    g = Gh(cmd=[sys.executable, "-c", "pass"], env_file=f)
    assert dead not in repr(vars(g))
