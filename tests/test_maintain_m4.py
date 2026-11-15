"""Maintenance: publishing puts the maintenance files and the label scheme on a new build from
its first commit; the drift doctor on GitHub and in the poll; the token path and the identity
line ``repo-factory doctor`` prints. A fake ``gh``, a fake GitHub and a scripted model: no
model, no network."""

import json
import os
import secrets as pysecrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import yaml

from repo_factory.gh import Gh, PublishError, token_env
from repo_factory.maintain import agentsmd, doctor, github, identity, labels, switch, watch

from .loop_common import TextModel, make_loop
from .maintain_common import FakeGitHub, fixture_issues, ghub, maintained
from .test_loop_gate_publish import built, gh_calls, repos

pytestmark = pytest.mark.usefixtures("maintenance_on")

README = """# Clinicbook

Book patients in.

## Install

```sh
./run.sh            # starts on http://127.0.0.1:8080/
```

## Tests

```sh
./run.sh test
python -m pytest tests/acceptance
```
"""
FILES = {
    "README.md": README,
    "app.py": '"""The web app: routes and pages."""\n',
    "run.sh": "#!/bin/sh\nexec python3 app.py\n",
    "SCOPE.md": "# Scope: Clinicbook\n\n### F1: Book a patient\n\n### F2: Remind\n",
}


@pytest.fixture(autouse=True)
def fake_gh(tmp_path, monkeypatch):
    d = tmp_path / "gh"
    monkeypatch.setenv("FAKE_GH_DIR", str(d))
    for k, v in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@t"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@t"),
    ):
        monkeypatch.setenv(k, v)
    return d


def show(bare, path, ref="main"):
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=bare, capture_output=True, text=True
    ).stdout


# -- publish: the maintenance files in the first commit, labels and settings after create --------
def test_publish_carries_only_the_machine_read_policy_by_default(tmp_path, fake_gh):
    """``publish.maintenance_files: policy``: .github/repo-factory.yml and labels.yml in the
    first commit, the labels after create; AGENTS.md, CI and the rest are the product's."""
    lp, iid, _ = built(tmp_path, {"files": FILES}, publish={"enabled": True})
    assert lp.gate(iid)["state"] == "ready"
    out = lp.publish(iid)
    assert out["state"] == "published", out.get("reason")
    r = repos(fake_gh)["owner/clinicbook"]
    ls = set(
        subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "main"],
            cwd=r["bare"],
            capture_output=True,
            text=True,
        ).stdout.split()
    )
    assert {".github/repo-factory.yml", ".github/labels.yml"} <= ls
    for gone in (
        "CLAUDE.md",
        ".github/workflows/ci.yml",
        ".github/workflows/labels.yml",
        ".github/scripts/check_agents_md.py",
        ".github/dependabot.yml",
        ".github/ISSUE_TEMPLATE/bug.yml",
        ".github/pull_request_template.md",
    ):
        assert gone not in ls, gone
    pol = yaml.safe_load(show(r["bare"], ".github/repo-factory.yml"))
    assert pol["status"] == "maintained" and pol["revise"]["trigger"] == "go"
    assert sorted(x["name"] for x in r["labels"]) == sorted(labels.NAMES)
    # the build wrote no AGENTS.md (Beliefline, 2026-10-11): repo-factory renders one, never none
    agents = show(r["bare"], "AGENTS.md")
    assert agents.startswith("# AGENTS.md — Clinicbook") and "\n./run.sh test   #" in agents
    assert "repo-factory runs these commands exactly as written" in agents
    assert "job `agents-md`" not in agents  # no ci.yml claims in this mode
    m = out["publish"]["maintenance"]
    assert m["files"] == [".github/repo-factory.yml", ".github/labels.yml", "AGENTS.md"]
    assert m["rendered"] == "AGENTS.md" and "CLAUDE.md is missing" in m["doctor"]


def test_publish_keeps_the_products_own_agents_md(tmp_path, fake_gh):
    own = "# AGENTS.md\n\nThe product's own instructions.\n"
    lp, iid, _ = built(tmp_path, {"files": {**FILES, "AGENTS.md": own}}, publish={"enabled": True})
    lp.gate(iid)
    out = lp.publish(iid)
    assert out["state"] == "published", out.get("reason")
    assert show(repos(fake_gh)["owner/clinicbook"]["bare"], "AGENTS.md") == own
    m = out["publish"]["maintenance"]
    assert m["files"] == [".github/repo-factory.yml", ".github/labels.yml"] and m["doctor"] == []


def test_publish_carries_the_maintenance_files_and_labels_from_the_first_commit(tmp_path, fake_gh):
    lp, iid, _ = built(
        tmp_path, {"files": FILES}, publish={"enabled": True, "maintenance_files": "all"}
    )
    assert lp.gate(iid)["state"] == "ready"
    out = lp.publish(iid)
    assert out["state"] == "published", out.get("reason")
    r = repos(fake_gh)["owner/clinicbook"]
    log = subprocess.run(
        ["git", "log", "--format=%s", "main"], cwd=r["bare"], capture_output=True, text=True
    ).stdout.split("\n")
    assert [x for x in log if x] == ["Clinicbook 0.1.0"], "one commit carries everything"
    ls = set(
        subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "main"],
            cwd=r["bare"],
            capture_output=True,
            text=True,
        ).stdout.split()
    )
    assert {
        "AGENTS.md",
        "CLAUDE.md",
        ".github/repo-factory.yml",
        ".github/labels.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/labels.yml",
        ".github/scripts/check_agents_md.py",
        ".github/scripts/sync_labels.py",
        ".github/dependabot.yml",
        ".github/ISSUE_TEMPLATE/bug.yml",
        ".github/pull_request_template.md",
        "CHANGELOG.md",
        ".gitignore",
    } <= ls
    pol = yaml.safe_load(show(r["bare"], ".github/repo-factory.yml"))
    assert pol["status"] == "maintained" and pol["revise"]["trigger"] == "go"
    agents = show(r["bare"], "AGENTS.md")
    assert agents.startswith("# AGENTS.md — Clinicbook") and "./run.sh test" in agents
    assert "| `app.py` | The web app: routes and pages. |" in agents
    assert "0.1.0" in show(r["bare"], "CHANGELOG.md")
    ci = yaml.safe_load(show(r["bare"], ".github/workflows/ci.yml"))
    assert ci["permissions"] == {"contents": "read"} and "self-hosted" not in json.dumps(ci)
    # the label scheme right after the repo was created, and no repository setting changed
    assert sorted(x["name"] for x in r["labels"]) == sorted(labels.NAMES)
    assert "delete_branch_on_merge" not in r and "alerts" not in r
    calls = gh_calls(fake_gh)
    create = next(i for i, c in enumerate(calls) if c[:2] == ["repo", "create"])
    first_label = next(i for i, c in enumerate(calls) if c[:2] == ["label", "create"])
    assert create < first_label
    m = out["publish"]["maintenance"]
    assert "AGENTS.md" in m["files"] and m["labels"]["created"] == len(labels.NAMES)
    assert "settings" not in m
    assert m["doctor"] == [] and m["errors"] == []


def test_publish_without_maintenance_is_a_bare_repo_as_before(tmp_path, fake_gh):
    lp, iid, _ = built(
        tmp_path, {"files": FILES}, publish={"enabled": True, "maintenance_files": "none"}
    )
    lp.gate(iid)
    out = lp.publish(iid)
    assert out["state"] == "published" and "maintenance" not in out["publish"]
    ls = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "main"],
        cwd=repos(fake_gh)["owner/clinicbook"]["bare"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert "AGENTS.md" not in ls and ".github/repo-factory.yml" not in ls
    assert not any(c[:2] == ["label", "create"] for c in gh_calls(fake_gh))


def test_the_review_reads_agents_md_and_can_fix_it(tmp_path, fake_gh):
    fix = {
        "file": "AGENTS.md",
        "quote": "Book patients in.",
        "problem": "fine",
        "replacement": "Book patients in, on one machine.",
    }
    hon = TextModel(review=lambda p: [fix])
    lp, iid, _ = built(
        tmp_path,
        {"files": FILES},
        honesty=hon,
        publish={"enabled": True, "maintenance_files": "all"},
    )
    lp.gate(iid)
    out = lp.publish(iid)
    assert out["state"] == "published"
    assert "--- AGENTS.md\n# AGENTS.md — Clinicbook" in hon.of("review")[0]
    agents = show(repos(fake_gh)["owner/clinicbook"]["bare"], "AGENTS.md")
    assert "Book patients in, on one machine." in agents


def test_a_module_without_a_docstring_gets_a_plain_row_not_a_placeholder(tmp_path, fake_gh):
    lp, iid, _ = built(
        tmp_path,
        {"files": {**FILES, "store.py": "x = 1\n"}},
        publish={"enabled": True, "maintenance_files": "all"},
    )
    lp.gate(iid)
    out = lp.publish(iid)
    agents = show(repos(fake_gh)["owner/clinicbook"]["bare"], "AGENTS.md")
    assert "| `store.py` | Not described yet" in agents and "(describe this module)" not in agents
    assert out["publish"]["maintenance"]["doctor"] == []


# -- the drift doctor on GitHub ----------------------------------------------------------------
class TreeHub(FakeGitHub):
    """The fake GitHub plus a tree and file contents on main, search and PR diffs."""

    def __init__(self, *a, files=None, search=None, diffs=None, **kw):
        super().__init__(*a, **kw)
        self.files, self.search_items, self.diffs = dict(files or {}), search or [], diffs or {}

    def __call__(self, args, cwd=None):
        a = list(args)
        path = a[-1]
        u = urlsplit(path)
        base = f"repos/{self.repo}"
        if u.path == "search/issues":
            self.calls.append(("GET", path, None))
            q = parse_qs(u.query)["q"][0]
            items = [
                i
                for i in self.search_items
                if f"repo:{i['repository_url'].split('repos/')[1]}" in q
            ]
            return 0, json.dumps({"total_count": len(items), "items": items}), ""
        if u.path == f"{base}/git/trees/main":
            self.calls.append(("GET", path, None))
            dirs = {str(p) for f in self.files for p in Path(f).parents if str(p) != "."}
            tree = [{"path": d, "type": "tree"} for d in sorted(dirs)] + [
                {"path": f, "type": "blob"} for f in sorted(self.files)
            ]
            return 0, json.dumps({"tree": tree}), ""
        if u.path.startswith(f"{base}/contents/"):
            self.calls.append(("GET", path, None))
            f = unquote(u.path[len(f"{base}/contents/") :])
            return (0, self.files[f], "") if f in self.files else (1, "", "HTTP 404: Not Found")
        if "Accept: application/vnd.github.diff" in a:
            self.calls.append(("GET", path + " (diff)", None))
            return 0, self.diffs.get(int(path.rsplit("/", 1)[1]), ""), ""
        return super().__call__(args, cwd)


def product_files(agents: str | None = None, workflow: str | None = None) -> dict:
    agents = (
        agents
        if agents is not None
        else (
            "# AGENTS.md — Plots\n\n## Setup, run, test\n\n```sh\n./plots.sh   # serve\n"
            "./plots.sh test\n```\n\n## Architecture in brief\n\n| Path | Holds |\n|---|---|\n"
            "| `plots.sh` | The launcher. |\n| `plots/web.py` | Routes. |\n"
        )
    )
    return {
        "AGENTS.md": agents,
        "README.md": "# Plots\n\n```sh\n./plots.sh\n./plots.sh test\n```\n",
        "CLAUDE.md": "@AGENTS.md\n",
        ".github/repo-factory.yml": maintained(),
        ".github/labels.yml": labels.labels_yaml(),
        "plots.sh": "",
        "plots/__init__.py": "",
        "plots/web.py": "",
        ".github/workflows/ci.yml": workflow
        or ("on: push\njobs:\n  t:\n    runs-on: ubuntu-24.04\n    steps: []\n"),
    }


def test_doctor_on_github_is_current_then_flags_drift_and_a_self_hosted_runner():
    fake = TreeHub(files=product_files())
    r = github.Repo(ghub(fake), "o/plots")
    assert doctor.check_repo(r) == []
    files = product_files(
        workflow="on: push\njobs:\n  t:\n    runs-on:\n      - self-hosted\n"
        "      - linux\n    steps: []\n"
    )
    files["plots/store.py"] = ""  # a module the table does not name
    files["AGENTS.md"] = files["AGENTS.md"].replace("plots/web.py", "plots/gone.py")
    fake = TreeHub(files=files)
    probs = doctor.check_repo(github.Repo(ghub(fake), "o/plots"))
    assert any("`plots/gone.py`, which does not exist" in p for p in probs)
    assert any("`plots/store.py` is not in AGENTS.md's architecture section" in p for p in probs)
    assert any(
        "ci.yml: job t runs on 'self-hosted'" in p and doctor.SELF_HOSTED in p for p in probs
    )
    assert not [c for c in fake.calls if c[0] != "GET"], "the doctor only reads"
    # the poll's step: due weekly, Today rows, the box's summary
    rec = {}
    assert doctor.step(github.Repo(ghub(fake), "o/plots"), rec, now=1000.0, days=7)
    assert doctor.step(github.Repo(ghub(fake), "o/plots"), rec, now=1000.0 + 86400, days=7) is None
    assert doctor.due(rec, 1000.0 + 7 * 86400, 7) and not doctor.due(rec, 9e9, 0)
    rows = doctor.rows("o/plots", rec)
    assert [x["kind"] for x in rows] == ["self_hosted", "drift"]
    assert rows[1]["title"] == "AGENTS.md has drifted: 3 problems"  # gone.py, store.py, web.py
    assert rows[0]["url"].endswith("/tree/main/.github/workflows")
    assert doctor.summary(rec)["current"] is False and doctor.summary({}) is None


@pytest.mark.parametrize(
    "runs_on,hosted",
    [
        ("ubuntu-24.04", False),
        ("[self-hosted, linux]", True),
        ("self-hosted", True),
        ("\n      - self-hosted", True),
        ("\n      labels: [self-hosted]", True),
        # an allow-list: what cannot be told counts as not hosted
        ("${{ matrix.os }}", True),
        ("${{ vars.RUNNER }}", True),
        ("[linux, my-own-box]", True),
        ("my-own-box", True),
        ("\n      group: fleet", True),
        ("[ubuntu-latest]", False),
        ("windows-2022", False),
        ("macos-14", False),
    ],
)
def test_self_hosted_in_every_form(runs_on, hosted):
    wf = f"on: push\njobs:\n  t:\n    runs-on: {runs_on}\n    steps: []\n"
    assert agentsmd.self_hosted(wf) is hosted


@pytest.fixture
def world(tmp_path):
    fake = TreeHub(fixture_issues(), policy_text=maintained(), files=product_files())
    lp = make_loop(tmp_path)
    lp._gh = ghub(fake)
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper", "one_liner": "Allotment plots and rents."},
        publish={"repo": "o/plots", "url": "https://github.com/o/plots"},
    )
    return lp, fake, i["id"]


def test_the_poll_runs_the_doctor_weekly_and_today_shows_what_is_wrong(world):
    lp, fake, iid = world
    switch.set_on(lp.cfg.home, "o/plots", True)
    watch.tick(lp)
    rec = github.Cache(lp.cfg.home).repo("o/plots")
    assert rec["doctor"]["problems"] == [] and not [r for r in rec["rows"] if r["kind"] == "drift"]
    n = sum(1 for c in fake.calls if "git/trees" in c[1])
    fake.files["AGENTS.md"] = fake.files["AGENTS.md"].replace("plots/web.py", "plots/old.py")
    watch.tick(lp)
    assert sum(1 for c in fake.calls if "git/trees" in c[1]) == n, "not again within the week"
    lp.cfg.data["maintain"]["doctor_days"] = 0.0000001  # due now
    watch.tick(lp)
    page = watch.rows_of(lp.cfg.home, "o/plots")
    assert "plots/old.py" in page["doctor"][0]
    assert doctor.summary(github.Cache(lp.cfg.home).repo("o/plots"))["current"] is False


# -- the token path (D1) ------------------------------------------------------------------------
def token():
    return "github_pat_" + pysecrets.token_hex(20)  # built at runtime: never a literal


def token_file(tmp_path, value, mode=0o600, name="maintain.env"):
    p = tmp_path / name
    p.write_text(f"# the maintenance token\nexport GH_TOKEN='{value}'\n")
    p.chmod(mode)
    return p


ECHO_GH = """import json, os, sys
a = sys.argv[1:]
tok = os.environ.get("GH_TOKEN")
open(os.environ["ECHO_OUT"], "a").write(json.dumps({"args": a, "has": bool(tok),
    "match": tok == os.environ.get("EXPECT")}) + "\\n")
if a[:3] == ["api", "-i", "user"]:
    print("HTTP/2.0 200 OK")
    print("Github-Authentication-Token-Expiration: 2027-10-08 12:00:00 UTC")
    print("Content-Type: application/json")
    print()
    print(json.dumps({"login": "factory-bot" if tok else "owner"}))
elif a[:2] == ["api", "user"]:
    print("owner")
elif a[0] == "api" and a[1].startswith("repos/"):
    sys.exit(0 if a[1] != "repos/o/secret" else 1)
"""


@pytest.fixture
def echo_gh(tmp_path, monkeypatch):
    script = tmp_path / "echo_gh.py"
    script.write_text(ECHO_GH)
    out = tmp_path / "echo.jsonl"
    monkeypatch.setenv("ECHO_OUT", str(out))
    return [sys.executable, str(script)], out


def test_the_token_reaches_only_the_gh_subprocess(tmp_path, echo_gh, monkeypatch):
    cmd, out = echo_gh
    value = token()
    monkeypatch.setenv("EXPECT", value)
    gh = Gh(cmd=cmd, env_file=token_file(tmp_path, value))
    gh("api", "user")
    rec = json.loads(out.read_text().splitlines()[-1])
    assert rec["has"] and rec["match"]
    assert "GH_TOKEN" not in os.environ and value not in repr(vars(gh))
    plain = Gh(cmd=cmd)
    plain("api", "user")
    assert not json.loads(out.read_text().splitlines()[-1])["has"]
    auth = gh.git_auth()
    assert auth[1] == "credential.https://github.com.helper=" and "auth git-credential" in auth[3]
    assert plain.git_auth() == [] and plain.env() is None


def test_a_broken_token_file_fails_like_gh_and_never_shows_the_value(tmp_path, echo_gh):
    cmd, _ = echo_gh
    value = token()
    loose = token_file(tmp_path, value, mode=0o644, name="loose.env")
    for f, why in ((loose, "readable by other users"), (tmp_path / "none.env", "cannot be read")):
        gh = Gh(cmd=cmd, env_file=f)
        code, outp, err = gh._run(["api", "user"], None)
        assert code != 0 and why in err and value not in err + outp
        with pytest.raises(PublishError) as e:
            gh("api", "user")
        assert value not in str(e.value)
    empty = tmp_path / "empty.env"
    empty.write_text("OTHER=1\n")
    empty.chmod(0o600)
    with pytest.raises(PublishError, match="no GH_TOKEN"):
        token_env(empty)
    assert token_env(token_file(tmp_path, value, name="ok.env")) == {"GH_TOKEN": value}


def test_identity_says_which_login_or_token_and_its_expiry(tmp_path, echo_gh, monkeypatch):
    cmd, out = echo_gh
    value = token()
    monkeypatch.setenv("EXPECT", value)
    owner = Gh(cmd=cmd)
    now = time.mktime((2026, 10, 9, 0, 0, 0, 0, 0, -1))
    d = identity.describe(
        Gh(cmd=cmd, env_file=token_file(tmp_path, value)),
        owner_gh=owner,
        repos=["o/plots", "o/secret"],
        now=now,
    )
    assert d["kind"] == "bot" and d["login"] == "factory-bot" and d["fine_grained"]
    assert d["expires"] == "2027-10-08" and d["reach"] == [1, 2] and d["status"] == "warn"
    assert "a fine-grained token for factory-bot (a bot account)" in d["line"]
    assert "reaches 1 of 2 maintained repos" in d["line"] and value not in json.dumps(d)
    d = identity.describe(
        Gh(cmd=cmd, env_file=token_file(tmp_path, value)),
        owner_login="factory-bot",
        repos=["o/plots"],
        now=now,
    )
    assert d["kind"] == "token" and d["status"] == "ok" and "(your account)" in d["line"]
    # near and past the expiry
    soon = time.mktime((2027, 9, 20, 0, 0, 0, 0, 0, -1))
    assert (
        identity.describe(
            Gh(cmd=cmd, env_file=token_file(tmp_path, value)), owner_login="factory-bot", now=soon
        )["status"]
        == "warn"
    )
    late = time.mktime((2027, 11, 1, 0, 0, 0, 0, 0, -1))
    d = identity.describe(
        Gh(cmd=cmd, env_file=token_file(tmp_path, value)), owner_login="x", now=late
    )
    assert d["status"] == "fail" and "(expired)" in d["line"]


def fake_run(status=200, headers=(), login="owner", code=0, err=""):
    def run(args, cwd=None):
        if args[:3] == ["api", "-i", "user"]:
            head = [f"HTTP/2.0 {status} X", *headers]
            return code, "\n".join(head) + "\n\n" + json.dumps({"login": login}), err
        return 0, "", ""

    return run


def test_identity_of_the_owner_login_a_classic_token_and_a_refused_one(tmp_path):
    d = identity.describe(Gh(run=fake_run(headers=["X-OAuth-Scopes: repo, workflow, gist"])))
    assert (
        d["kind"] == "owner"
        and d["status"] == "warn"
        and d["scopes"] == ["repo", "workflow", "gist"]
    )
    assert "the owner's gh login (owner; scopes repo, workflow, gist)" in d["line"]
    f = token_file(tmp_path, token())
    classic = Gh(run=fake_run(headers=["X-OAuth-Scopes: repo, workflow"]), env_file=f)
    d = identity.describe(classic, owner_login="owner")
    assert (
        d["status"] == "warn" and "a classic token" in d["line"] and "workflow files" in d["line"]
    )
    refused = Gh(run=fake_run(status=401, code=1, err="HTTP 401: Bad credentials"), env_file=f)
    assert identity.describe(refused)["status"] == "fail"
    shaped = "ghp_" + pysecrets.token_hex(18)
    broken = Gh(run=lambda a, c=None: (1, "", f"bad {shaped} here"))
    d = identity.describe(broken)
    assert shaped not in d["line"] and "[token]" in d["line"]


def test_doctor_line_for_factory_doctor_on_a_scratch_home(tmp_path, echo_gh, monkeypatch):
    cmd, _ = echo_gh
    value = token()
    monkeypatch.setenv("EXPECT", value)
    home = tmp_path / "loop"
    home.mkdir()
    f = token_file(tmp_path, value)
    (home / "loop.yaml").write_text(
        yaml.safe_dump(
            {
                "publish": {"gh_command": cmd},
                "maintain": {"token_env_file": str(f), "repos": ["o/plots"]},
            }
        )
    )
    status, line = identity.doctor(home)
    assert status == "ok" and line.startswith(
        "a fine-grained token for factory-bot (a bot account)"
    )
    assert "reaches 1 of 1 maintained repo" in line and value not in line
    (home / "loop.yaml").write_text(yaml.safe_dump({"publish": {"gh_command": cmd}}))
    status, line = identity.doctor(home)
    assert status == "warn" and line.startswith("the owner's gh login (owner)")
    (home / "loop.yaml").write_text(yaml.safe_dump({"maintain": {"enabled": False}}))
    assert identity.doctor(home) == ("ok", "maintenance is off (maintain.enabled: false)")


def test_maintenance_uses_the_token_file_for_gh_and_for_a_fix_s_push(tmp_path):
    lp = make_loop(tmp_path)
    f = token_file(tmp_path, token())
    lp.cfg.data["maintain"]["token_env_file"] = str(f)
    gh = watch.gh_for(lp)
    assert gh.env_file == f and gh.cmd == lp.cfg["publish"]["gh_command"]
    lp.cfg.data["maintain"]["token_env_file"] = None
    assert watch.gh_for(lp) is lp.gh
    from repo_factory.maintain import revise

    src = Path(revise.__file__).read_text()
    assert "self.remote(" not in src and 'git("push"' not in src
    # a fix pushes from the host's own clone only, with the same identity
    assert "auth, env = self.git_auth()" in src
    assert "carry.push(dest, self.full, branch, auth, env)" in src
