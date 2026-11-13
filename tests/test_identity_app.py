"""The identity seam with repo-factory's own GitHub App (GA-PLAN.md §2.6–2.8): ``maintain.identity``
auto, the app's ``Gh`` narrowed to one repository per call, the poll acting as the bot, an
issue the bot filed counted as repo-factory's own, commits authored as the bot, a fix's push with
the push scope, ``whoami``'s line and ``repo-factory doctor``'s, and no behaviour change without an
app. The fake GitHub from tests/fake_github.py; ``gh`` is a fake run."""

import json
import subprocess
import time

import pytest
import yaml

from repo_factory import github_app as G
from repo_factory.gh import Gh
from repo_factory.maintain import identity, poll, revise, triage
from repo_factory.settings import write as settings
from repo_factory.ui import api

from .fake_github import FakeGitHub
from .loop_common import FakeModel, make_loop
from .test_maintain_triage import NOW, stamp
from .test_maintain_triage import FakeGitHub as Hub

pytestmark = [
    pytest.mark.skipif(G.available() is not None, reason=G.available() or ""),
    pytest.mark.usefixtures("maintenance_on"),
]
REPO = "o/plots"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A loop whose gh is a fake hub, and the fake GitHub for the app (not installed yet)."""
    fake = FakeGitHub(owner="o", slug="factory-o")
    monkeypatch.setenv(G.API_ENV, fake.url)
    monkeypatch.setenv(G.DIR_ENV, str(tmp_path / "github"))
    G._APPS.clear()
    hub = Hub([], policy_text=None)
    lp = make_loop(tmp_path)
    lp._gh = Gh(run=hub)
    made = []

    def app_gh(the_app, scope, repos, cmd):  # the app's gh runs the hub, not gh
        g = the_app.gh(scope, repos, cmd=cmd)
        g._run = hub
        made.append((scope, tuple(repos or ())))
        return g

    monkeypatch.setattr(identity, "app_gh", app_gh)
    yield lp, hub, fake, made
    fake.close()
    G._APPS.clear()


def install(fake, repos=(REPO, "o/repo_factory")):
    store = G.Store()
    G.exchange(fake.code(), store)
    fake.install(list(repos))
    G.app_for().refresh()
    return G.app_for()


# -- without an app: exactly as before ------------------------------------------------------


def test_the_setting_is_checked_and_login_wins_over_an_installed_app(world):
    lp, hub, fake, made = world
    install(fake)
    for v in ("auto", "app", "token", "login"):
        assert settings.check("maintain.identity", v) == v
    with pytest.raises(ValueError, match="auto, app, token, login"):
        settings.check("maintain.identity", "bot")
    assert "identity" in settings.MAINTAIN
    lp.cfg.data["maintain"]["identity"] = "login"
    assert poll.gh_for(lp, REPO) is lp.gh and identity.resolve(lp.cfg) is None
    lp.cfg.data["maintain"]["identity"] = "token"
    assert poll.gh_for(lp, REPO) is lp.gh  # no token file: the login


# -- with the app installed ------------------------------------------------------------------
def test_the_app_acts_narrowed_to_one_repository_per_call(world):
    lp, hub, fake, made = world
    app = install(fake)
    g = poll.gh_for(lp, REPO)
    assert g.credential == "app" and g.login_name == "factory-o[bot]" and g.scope == "maintain"
    assert g.repos == (REPO,) and made[-1] == ("maintain", (REPO,))
    assert identity.login_of(g) == "factory-o[bot]" and g.login() == "factory-o[bot]"
    i = poll.gh_for(lp, "o/repo_factory", "issues")
    assert i.scope == "issues" and i.repos == ("o/repo_factory",)
    # the narrowed push identity for a fix's git push
    p = identity.narrow(g, "push")
    assert p.scope == "push" and p.repos == (REPO,) and p.login_name == "factory-o[bot]"
    env = p.env()
    assert env["GH_TOKEN"] == app.token("push", [REPO])
    assert fake.token_requests[-1] == {
        "permissions": {"contents": "write", "metadata": "read"},
        "repositories": ["plots"],
    }
    # the bot authors commits, with its noreply address
    assert identity.git_author(g) == [
        "-c",
        "user.name=factory-o[bot]",
        "-c",
        f"user.email={fake.bot_user_id}+factory-o[bot]@users.noreply.github.com",
    ]
    # a repository the installation does not reach: refused before any call, never the login
    out = poll.gh_for(lp, "o/elsewhere")
    code, _, err = out._subprocess(["api", "repos/o/elsewhere"])
    assert code == 4 and "not installed on o/elsewhere" in err
    assert out is not lp.gh


def test_describe_whoami_and_the_doctor_line_for_the_app(world, tmp_path):
    lp, hub, fake, made = world
    app = install(fake)
    g = poll.gh_for(lp, REPO)
    d = identity.describe(g, repos=[REPO, "o/other"])
    assert d["kind"] == "app" and d["login"] == "factory-o[bot]" and d["reach"] == [1, 2]
    assert d["status"] == "warn" and "Add repositories" in d["line"]
    assert d["line"].startswith(
        "factory-o[bot], a GitHub App on your account (factory-o): "
        "issues, pull requests, contents; checks and actions read"
    )
    d = identity.describe(g, repos=[REPO])
    assert d["status"] == "ok" and "reaches 1 of 1 maintained repo" in d["line"]
    assert "ghs_" not in json.dumps(d) and "BEGIN" not in json.dumps(d)
    # repo-factory doctor's line on a scratch home
    home = tmp_path / "loop"
    home.mkdir()
    (home / "loop.yaml").write_text(yaml.safe_dump({"maintain": {"repos": [REPO]}}))
    status, line = identity.doctor(home)
    assert status == "ok" and line.startswith("factory-o[bot], a GitHub App")
    # uninstalled on GitHub: fail, with why; nothing falls back to the login
    fake.uninstall()
    app.forget()
    d = identity.describe(g, repos=[REPO])
    assert d["status"] == "fail" and "uninstalled" in d["line"]
    assert poll.gh_for(lp, REPO) is not lp.gh
    status, line = identity.doctor(home)
    assert status == "fail"


def test_the_poll_acts_as_the_bot_and_its_own_issues_count_as_the_owners(world):
    lp, hub, fake, made = world
    install(fake)
    hub.policy_text = (
        "repo-factory: 1\nstatus: maintained\nrevise:\n  trigger: owner\ntriage:\n  enabled: true\n"
    )
    hub.issues[5] = {
        "number": 5,
        "title": "Change idea from repo-factory",
        "body": "x",
        "user": {"login": "factory-o[bot]"},
        "author_association": "NONE",
        "labels": [{"name": "triage"}],
        "state": "open",
        "created_at": stamp(NOW - 3600),
        "updated_at": stamp(NOW - 600),
        "comments": 0,
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/5",
    }
    i, _ = lp.ledger.add("plots", "typed", title="Plotkeeper")
    lp.ledger.update(i["id"], state="published", publish={"repo": REPO})
    res = json.dumps(
        {
            "kind": "bug",
            "prio": "p2",
            "summary": "a bug",
            "needs_info": None,
            "duplicate_of": None,
            "answer": None,
        }
    )
    out = poll.maintain_tick(lp, model=FakeModel(res))
    assert made and made[0] == ("maintain", (REPO,))
    assert out["repos"][0]["triaged"] and out["repos"][0]["triaged"][0]["go"] is True
    assert "go" in hub.labels_of(5)
    # the same decision without the bot: a stranger's issue gets no go
    item = {"number": 6, "labels": [], "author": "factory-o[bot]", "is_pr": False}
    pol = {"status": "maintained", "revise": {"trigger": "owner", "kinds": ["bug"]}}
    r = {"kind": "bug", "prio": "p2"}
    assert triage.decide(item, r, pol, owner="o")["go"] is None
    assert triage.decide(item, r, pol, owner="o", bot="factory-o[bot]")["go"]


def test_a_commit_as_the_bot_and_a_push_with_the_push_token(world, tmp_path, monkeypatch):
    lp, hub, fake, made = world
    app = install(fake)
    g = poll.gh_for(lp, REPO)
    work = tmp_path / "w"
    root = work / "repo"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "a").write_text("1\n")
    subprocess.run(["git", "-C", str(root), "add", "a"], check=True)
    revise.commit(root, "as the bot", author=identity.git_author(g))
    who = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%an <%ae>"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert who == f"factory-o[bot] <{fake.bot_user_id}+factory-o[bot]@users.noreply.github.com>"
    # Fix.remote: the push scope's token in that one git call's environment
    seen = {}

    def fake_git(*args, cwd, check=True, env=None):
        seen["args"], seen["env"] = args, env
        return ""

    monkeypatch.setattr(revise, "git", fake_git)
    fix = revise.Fix.__new__(revise.Fix)
    fix.gh, fix.work = g, work
    revise.Fix.remote(fix, "push", "-q", "origin", "repo-factory/1-x")
    assert seen["args"][:2] == ("-c", "credential.https://github.com.helper=")
    assert seen["args"][-4:] == ("push", "-q", "origin", "repo-factory/1-x")
    assert seen["env"]["GH_TOKEN"] == app.token("push", [REPO])
    assert fake.token_requests[-1]["permissions"] == {"contents": "write", "metadata": "read"}
    # the app gone: the push fails in words, as a broken token file would
    fake.uninstall()
    app.forget()
    with pytest.raises(revise.ReviseError, match="uninstalled"):
        revise.Fix.remote(fix, "push", "-q", "origin", "repo-factory/1-x")


def test_the_settings_page_says_the_app_and_never_a_secret(world):
    lp, hub, fake, made = world
    install(fake)
    i, _ = lp.ledger.add("plots", "typed", title="Plotkeeper")
    lp.ledger.update(i["id"], state="published", publish={"repo": REPO})
    a = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a.loop, a.loop_cfg = (lambda: lp), (lambda: lp.cfg)
    code, _, body = api.handle("GET", "/api/maintain/summary", None, None, app=a)
    text = body.decode()
    assert code == 200 and "ghs_" not in text and "BEGIN" not in text
    d = json.loads(text)
    assert d["identity"]["kind"] == "app" and d["identity"]["bot_login"] == "factory-o[bot]"
    assert d["identity"]["mode"] == "auto" and d["settings"]["identity"] == "auto"
    assert d["identity"]["github"]["kind"] == "app"
    code, _, body = api.handle(
        "POST", "/api/maintain/settings", None, json.dumps({"identity": "login"}).encode(), app=a
    )
    assert code == 200
    assert (
        yaml.safe_load((lp.cfg.home / "loop.yaml").read_text())["maintain"]["identity"] == "login"
    )
    assert (
        api.handle(
            "POST", "/api/maintain/settings", None, json.dumps({"identity": "bot"}).encode(), app=a
        )[0]
        == 400
    )


def test_clean_hides_the_new_token_shapes():
    tok = "ghs_" + "A" * 8 + "_" + "eyJ" + "b" * 30 + ".c" * 2
    assert identity.clean(f"refused {tok} here") == "refused [token] here"
    assert identity.clean("github_pat_" + "x" * 30) == "[token]"
    assert identity.clean("eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10) == "[jwt]"
    kind = "RSA " + "PRIVATE KEY"  # built at run time: never a key-shaped literal
    assert "[key]" in identity.clean(f"-----BEGIN {kind}-----\nMII\n-----END {kind}-----")
    assert identity.clean("ghp_short") == "ghp_short"  # not a token shape
    assert time.time() > 0
