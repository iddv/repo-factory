"""The identity seam with repo-factory's own GitHub App: ``maintain.identity``
auto, the app's ``Gh`` narrowed to one repository per call, the poll acting as the bot, a
``go`` the bot put on counted as yours, commits authored as the bot, a fix's push with
the push scope, ``whoami``'s line and ``repo-factory doctor``'s, and no behaviour change without an
app. The fake GitHub from tests/fake_github.py; ``gh`` is a fake run."""

import json
import subprocess
import time

import pytest
import yaml

from repo_factory import github_app as G
from repo_factory.gh import Gh
from repo_factory.maintain import identity, policy, revise, switch, watch
from repo_factory.settings import write as settings
from repo_factory.ui import api

from .fake_github import FakeGitHub
from .loop_common import make_loop
from .maintain_common import NOW, stamp
from .maintain_common import FakeHub as Hub

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
    assert watch.gh_for(lp, REPO) is lp.gh and identity.resolve(lp.cfg) is None
    lp.cfg.data["maintain"]["identity"] = "token"
    assert watch.gh_for(lp, REPO) is lp.gh  # no token file: the login


# -- with the app installed ------------------------------------------------------------------
def test_the_app_acts_narrowed_to_one_repository_per_call(world):
    lp, hub, fake, made = world
    app = install(fake)
    g = watch.gh_for(lp, REPO)
    assert g.credential == "app" and g.login_name == "factory-o[bot]" and g.scope == "maintain"
    assert g.repos == (REPO,) and made[-1] == ("maintain", (REPO,))
    assert identity.login_of(g) == "factory-o[bot]" and g.login() == "factory-o[bot]"
    i = watch.gh_for(lp, "o/repo_factory", "issues")
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
    out = watch.gh_for(lp, "o/elsewhere")
    code, _, err = out._subprocess(["api", "repos/o/elsewhere"])
    assert code == 4 and "not installed on o/elsewhere" in err
    assert out is not lp.gh


def test_describe_whoami_and_the_doctor_line_for_the_app(world, tmp_path):
    lp, hub, fake, made = world
    app = install(fake)
    g = watch.gh_for(lp, REPO)
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
    assert watch.gh_for(lp, REPO) is not lp.gh
    status, line = identity.doctor(home)
    assert status == "fail"


def test_the_poll_acts_as_the_bot_and_a_go_the_bot_put_on_counts_as_yours(world, monkeypatch):
    lp, hub, fake, made = world
    install(fake)
    hub.policy_text = policy.render({"status": "maintained"})
    for n, who in ((5, "factory-o[bot]"), (6, "a-stranger")):
        hub.issues[n] = {
            "number": n,
            "title": f"Build this proposal {n}",
            "body": "x",
            "user": {"login": "factory-o[bot]"},
            "author_association": "NONE",
            "labels": [{"name": "bug"}, {"name": "go"}],
            "state": "open",
            "created_at": stamp(NOW - 3600 + n),
            "updated_at": stamp(NOW - 600),
            "comments": 0,
            "assignees": [],
            "html_url": f"https://github.com/{REPO}/issues/{n}",
        }
        hub.label(n, "go", who)
    i, _ = lp.ledger.add("plots", "typed", title="Plotkeeper")
    lp.ledger.update(i["id"], state="published", publish={"repo": REPO})
    switch.set_on(lp.cfg.home, REPO, True)
    started = []
    monkeypatch.setattr(
        revise,
        "start",
        lambda cfg, full, n, task: started.append(n) or {"pid": None, "task": str(task)},
    )
    watch.tick(lp)
    assert made and made[0] == ("maintain", (REPO,))
    assert started == [5], "the bot's go is yours; a stranger's is not"
    assert [m for m, *_ in hub.calls if m not in ("GET", "QUERY")] == [], "nothing written"


def test_a_commit_as_the_bot_and_a_push_with_the_push_token(world, tmp_path, monkeypatch):
    lp, hub, fake, made = world
    app = install(fake)
    g = watch.gh_for(lp, REPO)
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
    # Fix.git_auth: the push scope's token, for the host's own push (carry.push)
    fix = revise.Fix.__new__(revise.Fix)
    fix.gh, fix.work = g, work
    auth, env = revise.Fix.git_auth(fix)
    assert auth[:2] == ["-c", "credential.https://github.com.helper="]
    assert env["GH_TOKEN"] == app.token("push", [REPO])
    assert fake.token_requests[-1]["permissions"] == {"contents": "write", "metadata": "read"}
    # the app gone: the push fails in words, as a broken token file would
    fake.uninstall()
    app.forget()
    with pytest.raises(revise.ReviseError, match="uninstalled"):
        revise.Fix.git_auth(fix)


def test_the_settings_page_says_the_app_and_never_a_secret(world):
    lp, hub, fake, made = world
    install(fake)
    i, _ = lp.ledger.add("plots", "typed", title="Plotkeeper")
    lp.ledger.update(i["id"], state="published", publish={"repo": REPO})
    a = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a.loop, a.loop_cfg = (lambda: lp), (lambda: lp.cfg)
    text = json.dumps(a.maintenance())
    assert "ghs_" not in text and "BEGIN" not in text
    assert json.loads(text)["settings"]["identity"] == "auto"
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
