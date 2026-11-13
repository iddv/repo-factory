"""Settings › Publishing › GitHub's routes and actions: the manifest flow round
trip on the fake GitHub (create, the callback on 127.0.0.1, the key and the app id stored at
0600), the state checks (missing, stale, reused, wrong: nothing written before the state
passes), the install's setup redirect, refresh, replace key and remove as background jobs
the page polls, the doors (the page and the command line; never an agent), and
no request waiting on GitHub."""

import json
import stat
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from repo_factory import github_app as G
from repo_factory import registry as R
from repo_factory.ui import api

from .fake_github import FakeGitHub
from .loop_common import make_loop

pytestmark = pytest.mark.skipif(G.available() is not None, reason=G.available() or "")
UI = {"origin": "ui", "name": "ui", "actions": None}
CLI = {"origin": "cli", "name": "cli", "actions": None}
MCP = {"origin": "mcp", "name": "mcp", "actions": None}
AGENT = {"origin": "agent:RC", "name": "RC", "actions": None}


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake = FakeGitHub(owner="owner", slug="factory-owner")
    monkeypatch.setenv(G.API_ENV, fake.url)
    monkeypatch.setenv(G.DIR_ENV, str(tmp_path / "github"))
    monkeypatch.setenv(G.SPAWN_ENV, "0")  # jobs run inline: deterministic
    G._APPS.clear()
    lp = make_loop(tmp_path)
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    app.loop, app.loop_cfg = (lambda: lp), (lambda: lp.cfg)
    yield lp, app, fake
    fake.close()
    G._APPS.clear()


def get(app, path, query=None):
    code, ctype, data = api.handle("GET", path, query, None, app=app)
    return code, ctype, data.decode()


def act(app, kind, who=UI, confirm=True, **fields):
    body = {"kind": kind, "subject": {"kind": "loop"}, "fields": fields, "confirm": confirm}
    code, _, data = api.handle(
        "POST", "/api/act", None, json.dumps(body).encode(), app=app, identity=who
    )
    return code, json.loads(data)


def create(app, host="127.0.0.1:8766", port=8766):
    code, out = act(app, "github_app_create", host=host, port=port)
    assert code == 200, out
    return out


# -- the manifest flow round trip ---------------------------------------------------------
def test_the_manifest_flow_round_trips_on_loopback(world):
    lp, app, fake = world
    code, _, text = get(app, "/api/github/app")
    v = json.loads(text)
    assert code == 200 and v["state"] == "none" and v["app"] is None and v["available"]
    out = create(app)
    assert "Opening GitHub" in out["say"]
    # the page gets what it posts: the manifest and where to post it, nothing secret
    store = G.Store()
    pending = store.pending()
    assert pending and pending["host"] == "127.0.0.1" and pending["port"] == 8766
    assert stat.S_IMODE(store.pending_path.stat().st_mode) == 0o600
    assert json.loads(get(app, "/api/github/app")[2])["state"] == "creating"
    # what the action handed the page (the registry's result, through api/act's "result")
    # the redirect in the manifest is this page's own loopback address
    state = pending["state"]
    m = G.manifest("127.0.0.1:8766", 8766, "owner")
    assert m["redirect_url"] == "http://127.0.0.1:8766/api/github/app/callback"
    # GitHub sends the browser back with the code and the state
    code_ = fake.code()
    code, ctype, text = get(app, "/api/github/app/callback", f"code={code_}&state={state}")
    assert code == 200 and ctype.startswith("text/html")
    assert "url=../../../#/settings/github?callback=created" in text
    assert code_ not in text and "ghs_" not in text
    # the exchange ran (inline here): the key at 0600, the facts without a secret
    assert store.state() == "created"
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    rec = store.read()
    assert rec["app_id"] == fake.app_id and rec["slug"] == "factory-owner"
    assert "BEGIN" not in store.app_path.read_text()
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "created" and v["job"] == {**v["job"], "what": "exchange", "state": "done"}
    assert v["app"]["install_url"] == "https://github.com/apps/factory-owner/installations/new"
    assert "BEGIN" not in json.dumps(v) and "ghs_" not in json.dumps(v)
    # the owner installs it on GitHub; the setup redirect trusts nothing in the query and
    # reads the installation back with the app's own JWT
    fake.install(["owner/plots", "owner/repo_factory"])
    code, ctype, text = get(
        app, "/api/github/app/setup", "installation_id=999999&setup_action=install"
    )
    assert code == 200 and "url=../../../#/settings/github?setup=done" in text
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "installed" and v["app"]["installation_id"] == 777
    assert v["app"]["repos"] == ["owner/plots", "owner/repo_factory"]
    assert v["app"]["bot_login"] == "factory-owner[bot]" and v["app"]["bot_user_id"]
    assert v["app"]["manage_url"] == "https://github.com/settings/installations/777"
    # a token is minted and the fake saw every JWT as valid
    assert G.app_for().token("issues", ["owner/repo_factory"]).startswith("ghs_")
    assert fake.jwt_errors == []
    # a second creation is refused while an app exists
    code, out = act(app, "github_app_create", host="127.0.0.1:8766", port=8766)
    assert code == 409 and "remove it first" in out["error"]


def test_the_callback_writes_nothing_before_the_state_passes(world):
    lp, app, fake = world
    store = G.Store()
    # no creation in flight
    code, _, text = get(app, "/api/github/app/callback", f"code={fake.code()}&state=abc")
    assert code == 200 and "callback=stale" in text and "works once" in text
    assert store.read() is None and not store.key_path.exists()
    assert not [r for r in fake.requests if "conversions" in r[1]]
    # a wrong state uses the pending one up: the right one no longer passes either
    create(app)
    state = store.pending()["state"]
    code, _, text = get(app, "/api/github/app/callback", f"code={fake.code()}&state=wrong")
    assert "callback=stale" in text and store.read() is None
    code, _, text = get(app, "/api/github/app/callback", f"code={fake.code()}&state={state}")
    assert "callback=stale" in text and store.read() is None
    # a stale one (ten minutes)
    create(app)
    p = json.loads(store.pending_path.read_text())
    p["created"] = time.time() - G.PENDING_TTL_S - 5
    store.pending_path.write_text(json.dumps(p))
    code, _, text = get(app, "/api/github/app/callback", f"code={fake.code()}&state={p['state']}")
    assert "callback=stale" in text and store.read() is None
    assert not [r for r in fake.requests if "conversions" in r[1]]
    # a missing code with a good state: the exchange fails in words, the state is spent
    create(app)
    state = store.pending()["state"]
    code, _, text = get(app, "/api/github/app/callback", f"state={state}")
    assert "callback=created" in text
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "failed" and "does not look right" in v["job"]["error"]
    # a code GitHub no longer knows
    create(app)
    state = store.pending()["state"]
    get(app, "/api/github/app/callback", f"code=nope-nope-nope&state={state}")
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "failed" and "no longer knows that code" in v["job"]["error"]
    # the page may start again after a failure
    assert act(app, "github_app_create", host="localhost:8790", port=8790)[0] == 200


def _age_pending(store, seconds):
    p = json.loads(store.pending_path.read_text())
    p["created"] = time.time() - seconds
    store.pending_path.write_text(json.dumps(p))
    return p


def test_create_waits_five_minutes_then_says_what_happened(world):
    """The owner's report (UP #1): Create pressed, the GitHub tab closed, the spinner spun.
    After WAIT_S the page gets Create back and a line about the try; a callback inside the
    pending record's ten minutes is still taken, so an app GitHub made late is kept."""
    lp, app, fake = world
    store = G.Store()
    create(app)
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "creating" and v["pending"] and v["attempt"] is None
    assert v["wait_s"] == G.WAIT_S == 300
    p = _age_pending(store, G.WAIT_S + 5)
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "none" and not v["pending"]
    assert v["attempt"]["how"] == "no_answer" and abs(v["attempt"]["at"] - p["created"]) < 1
    # GitHub sends the browser back at minute six: the app is kept, the line goes
    get(app, "/api/github/app/callback", f"code={fake.code()}&state={p['state']}")
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "created" and v["attempt"] is None
    # an hour on, an old try is no longer mentioned
    store.clear()
    create(app)
    _age_pending(store, G.ATTEMPT_SHOWN_S + 5)
    assert json.loads(get(app, "/api/github/app")[2])["attempt"] is None


def test_cancel_puts_create_back_and_keeps_a_late_app(world):
    lp, app, fake = world
    store = G.Store()
    code, out = act(app, "github_app_cancel", confirm=False)
    assert code == 200 and out["result"]["id"] is None
    create(app)
    code, out = act(app, "github_app_cancel", confirm=False)
    assert code == 200 and out["result"]["id"] == "cancelled" and "link" in out["say"]
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "none" and v["attempt"]["how"] == "cancelled"
    # Create may start again at once
    assert act(app, "github_app_create", host="127.0.0.1:8766", port=8766)[0] == 200
    store.cancel()
    # the cancelled try's callback, if GitHub made the app anyway: kept, never lost
    state = store.pending()["state"]
    get(app, "/api/github/app/callback", f"code={fake.code()}&state={state}")
    assert store.state() == "created"
    log = (lp.cfg.home / "actions.jsonl").read_text()
    assert "github_app_cancel" in log


def test_an_app_made_on_github_is_linked_never_made_twice(world):
    """A record lost or never written (the callback never came): the app's id and a key
    generated on GitHub link it; nothing is created and the installation is read back."""
    lp, app, fake = world
    store = G.Store()
    fake.install(["owner/plots"])
    # a wrong id or a wrong key: refused by GitHub, nothing kept
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        .decode()
    )
    code, out = act(app, "github_app_link", app_id=str(fake.app_id), pem=other)
    assert code == 200
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "failed" and v["job"]["what"] == "link"
    assert "did not accept that key" in v["job"]["error"]
    assert store.read() is None and not store.key_path.exists()
    assert act(app, "github_app_link", app_id="factory-owner", pem=fake.pem)[0] == 400
    assert act(app, "github_app_link", app_id=str(fake.app_id), pem="not a key")[0] == 400
    assert (
        act(app, "github_app_link", app_id=str(fake.app_id), pem=fake.pem, confirm=False)[0] == 409
    )
    # the right pair: linked and read back, no creation on GitHub
    code, out = act(app, "github_app_link", app_id=str(fake.app_id), pem=fake.pem)
    assert code == 200 and "Checking" in out["say"]
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "installed" and v["job"]["state"] == "done"
    assert v["app"]["app_id"] == fake.app_id and v["app"]["slug"] == "factory-owner"
    assert v["app"]["repos"] == ["owner/plots"] and v["app"]["has_key"]
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    assert "BEGIN" not in store.app_path.read_text() + store.job_path.read_text()
    assert not [r for r in fake.requests if "conversions" in r[1] or "apps/new" in r[1]]
    assert "BEGIN" not in (lp.cfg.home / "actions.jsonl").read_text()
    # a second link (or a create) is refused while an app is linked
    code, out = act(app, "github_app_link", app_id=str(fake.app_id), pem=fake.pem)
    assert code == 409 and "already has a GitHub App" in out["error"]
    assert act(app, "github_app_create", host="127.0.0.1:8766", port=8766)[0] == 409


def test_an_app_linked_before_its_install_says_so(world):
    lp, app, fake = world
    act(app, "github_app_link", app_id=str(fake.app_id), pem=fake.pem)
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "created" and v["job"]["state"] == "done"
    assert v["app"]["install_url"].endswith("/installations/new")


def test_the_host_must_be_loopback_by_name(world):
    lp, app, fake = world
    for host in ("buildbox:8765", "192.168.1.4:8765", "repo-factory.local"):
        code, out = act(app, "github_app_create", host=host, port=8765)
        assert code == 400 and "127.0.0.1" in out["error"], host
    assert act(app, "github_app_create", host="", port=8765)[0] == 400
    assert G.Store().pending() is None
    code, out = act(app, "github_app_create", host="localhost:8765", port=8765, mount="/")
    assert (
        code == 200
        and out["result"]["form"]["manifest"]["redirect_url"]
        == "http://localhost:8765/api/github/app/callback"
    )
    assert act(app, "github_app_create", host="localhost:8765", port=8765, mount="/x/y")[0] == 400
    code, out = act(app, "github_app_create", host="localhost:8765", port=8765, confirm=False)
    assert code == 409 and "confirm" in out["error"]


def test_refresh_key_and_remove_are_jobs_the_page_polls(world):
    lp, app, fake = world
    store = G.Store()
    G.exchange(fake.code(), store)
    fake.install(["owner/plots"])
    code, out = act(app, "github_app_refresh", confirm=False)
    assert code == 200 and "Reading" in out["say"]
    v = json.loads(get(app, "/api/github/app")[2])
    assert (
        v["state"] == "installed" and v["job"]["what"] == "refresh" and v["job"]["state"] == "done"
    )
    assert v["app"]["repos"] == ["owner/plots"]
    # replace the key: a wrong one is refused by GitHub and the file is untouched
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        .decode()
    )
    old = store.key_path.read_text()
    code, out = act(app, "github_app_key", pem=other)
    assert code == 200
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["job"]["what"] == "key" and v["job"]["state"] == "failed"
    assert "did not accept that key" in v["job"]["error"] and store.key_path.read_text() == old
    assert "BEGIN" not in store.job_path.read_text()
    code, out = act(app, "github_app_key", pem="not a key")
    assert code == 400
    code, out = act(app, "github_app_key", pem=fake.pem)
    assert code == 200
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["job"]["state"] == "done" and v["app"]["key_fingerprint"] == G.fingerprint(fake.pem)
    # the action log carries no key
    log = (lp.cfg.home / "actions.jsonl").read_text()
    assert "BEGIN" not in log and "github_app_key" in log
    # remove: uninstalled on GitHub, forgotten here, the page says where to delete it
    code, out = act(app, "github_app_remove")
    assert code == 200 and "Advanced" not in out["say"] and "uninstalled" in out["say"]
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "none" and v["app"] is None and v["job"]["state"] == "done"
    assert fake.installation is None and not store.key_path.exists()
    # the next maintenance identity is the login again
    from repo_factory.maintain import poll

    assert poll.gh_for(lp, "owner/plots") is lp.gh


def test_uninstalled_on_github_is_said_clearly_and_falls_back_to_nothing(world):
    lp, app, fake = world
    store = G.Store()
    G.exchange(fake.code(), store)
    fake.install(["owner/plots"])
    G.app_for().refresh()
    fake.uninstall()
    G.app_for().forget()
    from repo_factory.maintain import identity, poll

    g = poll.gh_for(lp, "owner/plots")
    assert g is not lp.gh
    d = identity.describe(g, repos=["owner/plots"])
    assert d["status"] == "fail" and "uninstalled" in d["line"]
    act(app, "github_app_refresh", confirm=False)
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "created" and v["job"]["state"] == "failed"
    assert "not installed on any account" in v["job"]["error"]
    assert v["app"]["install_url"].endswith("/installations/new")


def test_the_doors_are_the_page_and_the_command_line_only(world):
    lp, app, fake = world
    for kind in R.GITHUB_KINDS:
        code, out = act(app, kind, who=AGENT, host="127.0.0.1:1", port=1, pem="x")
        assert code == 403 and "Settings › Publishing › GitHub" in out["error"], kind
        code, out = act(app, kind, who=MCP, host="127.0.0.1:1", port=1, pem="x")
        assert code == 403, kind  # a door that left acts nowhere
    assert not set(R.GITHUB_KINDS) & set(R.agent_default_actions())
    assert G.Store().pending() is None
    code, out = act(app, "github_app_create", who=CLI, host="127.0.0.1:8766", port=8766)
    assert code == 200


def test_no_request_waits_on_github(world, monkeypatch):
    """PA's rule: the callback, the setup redirect and every action answer at once; the
    exchange and the refresh run in the background while GitHub is slow."""
    lp, app, fake = world
    monkeypatch.delenv(G.SPAWN_ENV)  # real background threads
    gate = threading.Event()
    real = fake.handle

    def slow(method, path, headers, body):
        if "conversions" in path or "installations" in path:
            gate.wait(20)
        return real(method, path, headers, body)

    fake.handle = slow
    create(app)
    state = G.Store().pending()["state"]
    t0 = time.time()
    code, _, text = get(app, "/api/github/app/callback", f"code={fake.code()}&state={state}")
    assert time.time() - t0 < 2 and "callback=created" in text
    v = json.loads(get(app, "/api/github/app")[2])
    assert v["state"] == "creating" and v["job"]["state"] == "running"
    gate.set()
    for _ in range(100):
        if G.Store().state() == "created":
            break
        time.sleep(0.05)
    assert G.Store().state() == "created"
    gate.clear()
    fake.install(["owner/plots"])
    t0 = time.time()
    code, _, text = get(app, "/api/github/app/setup", "installation_id=1")
    assert time.time() - t0 < 2 and "setup=done" in text
    t0 = time.time()
    code, out = act(app, "github_app_refresh", confirm=False)
    assert code in (200, 409) and time.time() - t0 < 2  # 409: the first refresh runs
    gate.set()
    for _ in range(100):
        if G.Store().state() == "installed":
            break
        time.sleep(0.05)
    assert G.Store().state() == "installed"


def test_forward_pages_are_relative_to_the_mount():
    assert "url=../../../#/settings/github" in G.forward_page("/api/github/app/callback")
    assert "url=../../../#/settings/github" in G.forward_page(
        "/brb/api/github/app/setup".removeprefix("/brb")
    )
    assert "url=./#/settings/github" in G.forward_page("/callback")
    assert "<script" not in G.forward_page("/x", note="<b>x</b>")


def test_the_cli_status_refresh_and_remove(world, capsys, monkeypatch):
    from repo_factory import cli

    lp, app, fake = world
    monkeypatch.setenv("REPO_FACTORY_HOME", str(lp.cfg.home))
    (lp.cfg.home / "loop.yaml").write_text(json.dumps(lp.cfg.data))
    cli.main(["github", "status"])
    out = capsys.readouterr().out
    assert "state: none" in out and "Settings › Publishing › GitHub" in out
    G.exchange(fake.code(), G.Store())
    fake.install(["owner/plots"])
    cli.main(["github", "refresh"])
    assert "1 repositories: owner/plots" in capsys.readouterr().out
    cli.main(["github", "status", "--json"])
    v = json.loads(capsys.readouterr().out)
    assert v["state"] == "installed" and "BEGIN" not in json.dumps(v)
    cli.main(["github", "remove"])
    assert "forgotten here" in capsys.readouterr().out
    assert G.Store().state() == "none"
    assert "link one you made" in (cli.main(["github", "status"]) or capsys.readouterr().out)
    # link and cancel from the command line
    pem = lp.cfg.home / "app.pem"
    pem.write_text(fake.pem)
    pem.chmod(0o600)
    cli.main(["github", "link", "--app-id", str(fake.app_id), "--file", str(pem), "--delete-file"])
    assert "linked: factory-owner" in capsys.readouterr().out and not pem.exists()
    G.Store().clear()
    create(app)
    cli.main(["github", "cancel"])
    assert "Stopped waiting" in capsys.readouterr().out
    cli.main(["github", "status"])
    assert "never came back" in capsys.readouterr().out
    q = parse_qs(urlsplit("x?a=1").query)
    assert q == {"a": ["1"]}
