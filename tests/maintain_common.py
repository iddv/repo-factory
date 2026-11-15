"""What the maintenance tests share: a fake GitHub behind a fake ``gh`` (``FakeGitHub``; with a
fix's and a release's writes and an issue's events, ``FakeHub``), a fixture product whose
origin is a local bare repository, a scripted session harness, and a fix of issue 14 on it.
No model, no network."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from repo_factory.gh import Gh
from repo_factory.harness.base import HarnessResult
from repo_factory.maintain import github, policy, revise

from .loop_common import make_loop

FIX = Path(__file__).parent / "fixtures" / "maintain"
REPO = "o/plots"
NOW = time.time()
OWNER = "owner"
TEST = "tests/acceptance/test_huge_numeric_id_gets_404.py"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@localhost"]


def stamp(ts):
    return github.iso(ts)


class FakeGitHub:
    """The REST calls maintenance makes, over in-memory issues, comments, PRs and check runs;
    every call is recorded as ``(method, path, payload)``."""

    def __init__(self, issues=(), policy_text=None, collaborators=(), checks=None, repo=REPO):
        self.repo, self.policy_text = repo, policy_text
        self.issues = {i["number"]: json.loads(json.dumps(i)) for i in issues}
        for i in self.issues.values():
            i.setdefault("state", "open")
            i.setdefault("html_url", f"https://github.com/{repo}/issues/{i['number']}")
        self.comments: dict[int, list] = {}
        self.collab = list(collaborators)
        self.checks = checks or {}
        self.calls, self.merged, self.next_id = [], [], 100
        self.release = None

    def add_pr(self, number, title, author, labels=(), sha="abc", association="NONE", draft=False):
        self.issues[number] = {
            "number": number,
            "title": title,
            "user": {"login": author},
            "author_association": association,
            "body": "",
            "labels": [{"name": x} for x in labels],
            "state": "open",
            "created_at": stamp(NOW - 3600),
            "updated_at": stamp(NOW - 3600),
            "pull_request": {},
            "draft": draft,
            "head": {"sha": sha},
            "html_url": f"https://github.com/{self.repo}/pull/{number}",
        }

    def labels_of(self, n):
        return [x["name"] for x in self.issues[n]["labels"]]

    def __call__(self, args, cwd=None):
        a = list(args)
        assert a[0] == "api", a
        method = a[a.index("-X") + 1] if "-X" in a else "GET"
        payload = (
            json.loads(Path(a[a.index("--input") + 1]).read_text()) if "--input" in a else None
        )
        path = a[-1]
        self.calls.append((method, path, payload))
        u = urlsplit(path)
        p, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        base = f"repos/{self.repo}"
        if not p.startswith(base):
            return 1, "", "HTTP 404: Not Found"
        rest = p[len(base) :]
        ok = lambda obj: (0, json.dumps(obj), "")  # noqa: E731
        if rest == "" and method == "GET":  # the repository: its default branch
            return ok({"full_name": self.repo, "default_branch": getattr(self, "branch", "main")})
        if rest == "/contents/.github/repo-factory.yml":
            return (
                (1, "", "gh: Not Found (HTTP 404)")
                if self.policy_text is None
                else (0, self.policy_text, "")
            )
        if rest == "/readme":
            return 0, "# Plots\n\nBack up with ./plots.sh backup.\n", ""
        if rest == "/issues" and method == "GET":
            out = [i for i in self.issues.values() if q.get("state", "open") in ("all", i["state"])]
            if q.get("labels"):
                want = set(q["labels"].split(","))
                out = [i for i in out if want <= {x["name"] for x in i["labels"]}]
            return ok(sorted(out, key=lambda i: -i["number"]))
        if rest == "/pulls":
            return ok(
                [i for i in self.issues.values() if "pull_request" in i and i["state"] == "open"]
            )
        m = re.fullmatch(r"/pulls/(\d+)", rest)
        if m:
            return ok({**self.issues[int(m.group(1))], "mergeable": True})
        m = re.fullmatch(r"/pulls/(\d+)/merge", rest)
        if m and method == "PUT":
            n = int(m.group(1))
            self.merged.append(n)
            self.issues[n]["state"] = "closed"
            return ok({"merged": True})
        m = re.fullmatch(r"/commits/([^/]+)/check-runs", rest)
        if m:
            state = self.checks.get(unquote(m.group(1)))
            runs = (
                []
                if state is None
                else [
                    {
                        "name": "test",
                        "status": "completed" if state != "pending" else "in_progress",
                        "conclusion": state if state != "pending" else None,
                        "completed_at": stamp(NOW),
                        "html_url": "https://github.com/x",
                    }
                ]
            )
            return ok({"total_count": len(runs), "check_runs": runs})
        if rest == "/tags":  # no version tag: release has nothing to do
            return ok(getattr(self, "tag_list", []))
        if rest == "/releases/latest":
            return (1, "", "HTTP 404: Not Found") if not self.release else ok(self.release)
        if rest == "/collaborators":
            return ok([{"login": x, "permissions": {"push": True}} for x in self.collab])
        m = re.fullmatch(r"/issues/(\d+)", rest)
        if m:
            n = int(m.group(1))
            if method == "PATCH":
                self.issues[n].update(payload)
            return ok(self.issues[n])
        m = re.fullmatch(r"/issues/(\d+)/comments", rest)
        if m:
            n = int(m.group(1))
            if method == "POST":
                self.next_id += 1
                c = {
                    "id": self.next_id,
                    "body": payload["body"],
                    "user": {"login": "owner"},
                    "created_at": stamp(time.time()),
                    "updated_at": stamp(time.time()),
                }
                self.comments.setdefault(n, []).append(c)
                return ok(c)
            return ok(self.comments.get(n, []))
        m = re.fullmatch(r"/issues/(\d+)/labels", rest)
        if m and method == "POST":
            n = int(m.group(1))
            for x in payload["labels"]:
                if x not in self.labels_of(n):
                    self.issues[n]["labels"].append({"name": x})
            return ok(self.issues[n]["labels"])
        m = re.fullmatch(r"/issues/(\d+)/labels/(.+)", rest)
        if m and method == "DELETE":
            n, name = int(m.group(1)), unquote(m.group(2))
            if name not in self.labels_of(n):
                return 1, "", "HTTP 404: Label does not exist"
            self.issues[n]["labels"] = [x for x in self.issues[n]["labels"] if x["name"] != name]
            return 0, "", ""
        return 1, "", f"fake github: unexpected {method} {path}"

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


def fixture_issues():
    return json.loads((FIX / "issues.json").read_text())


def ghub(fake):
    return Gh(run=fake)


def maintained(**over) -> str:
    return policy.render(over)


# -- a fixture product with one known open item -------------------------------------------------
APP = '''"""Plots: the HTTP answer for a plot id."""
PLOTS = {1: "A-01"}


def lookup(id_text):
    n = int(id_text)
    if n > 2 ** 63:
        raise OverflowError("id too large for the store")
    return PLOTS.get(n)


def status(id_text):
    try:
        found = lookup(id_text)
    except OverflowError:
        return 500
    return 200 if found else 404
'''
MARKED = '''"""A huge numeric id in a URL gets a 404, not a server error (SECURITY.md S5)."""
from app import status


def test_huge_id_is_404():
    st = status("9" * 30)
    assert st == 404, f"huge id answered {st}"


# Known open item: expected to fail until it is fixed (tests/acceptance/README.md).
import pytest as _pytest_open  # noqa: E402
_marks = globals().get('pytestmark', [])
pytestmark = (list(_marks) if isinstance(_marks, (list, tuple)) else [_marks]) + [
    _pytest_open.mark.xfail(strict=False, reason='Known security issue (low): huge id')]
'''
FILES = {
    "app.py": APP,
    "tests/test_app.py": "from app import status\n\n\ndef test_known_plot():\n"
    "    assert status('1') == 200\n",
    "tests/acceptance/conftest.py": "import sys\nfrom pathlib import Path\n"
    "sys.path.insert(0, str(Path(__file__).parents[2]))\n",
    "tests/acceptance/test_unknown_plot_is_404.py": '"""An unknown plot is a 404."""\nfrom app import status\n\n\n'
    "def test_unknown():\n    assert status('7') == 404\n",
    TEST: MARKED,
    "tests/acceptance/README.md": "# Acceptance tests\n\nRun them with `python -m pytest tests/acceptance`.\n\n"
    "## Known open items\n\nThese tests are marked as expected failures.\n\n"
    "- `test_huge_numeric_id_gets_404.py`: Known security issue (low): huge id\n",
    "AGENTS.md": "# AGENTS.md — Plots\n\n## Setup, run, test\n\n```sh\n"
    "python -m pip install pytest==8.3.3 && python -m pytest tests/acceptance\n```\n",
    "SECURITY.md": "| S5 | low | A huge numeric id in a URL causes a 500 error | open |\n",
    "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n## 0.1.0\n\n- First release.\n",
    ".github/workflows/ci.yml": "name: ci\n",
    ".gitignore": "__pycache__/\n*.pyc\n",
}


def make_origin(tmp: Path) -> Path:
    src, bare = tmp / "product", tmp / "origin.git"
    for rel, text in FILES.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    subprocess.run([*GIT, "add", "-A"], cwd=src, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "Plots 0.1.0"], cwd=src, check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return bare


class FakeHub(FakeGitHub):
    """The fake GitHub plus what a fix and a release write and read: pull requests, issues,
    tags, compare, the pulls of a commit, releases, the login (``gh api user``), an issue's
    events (who put which label on), its last edit, workflow files; ``repo clone`` clones the
    local bare repo. Every write is in ``writes()``."""

    def __init__(self, *a, origin=None, **kw):
        super().__init__(*a, **kw)
        self.origin, self.tag_list, self.compare_commits = origin, [], []
        self.commit_prs, self.releases, self.next_number = {}, [], 100
        self.events: dict[int, list] = {}
        self.files: dict[str, str] = {}
        self.edited: dict[int, dict] = {}  # issue -> {"at": iso, "by": login}: a body edit
        self.clock = time.time() - 3600

    def tick(self):
        """GitHub's clock, one second on: each event here is later than the one before."""
        self.clock += 1
        return stamp(self.clock)

    def label(self, n, name, actor):
        self.events.setdefault(n, []).append(
            {
                "event": "labeled",
                "label": {"name": name},
                "actor": {"login": actor},
                "created_at": self.tick(),
            }
        )

    def edit(self, n, actor, title=None, body=None):
        """``actor`` changes issue ``n``'s title (a ``renamed`` event) or body."""
        if title is not None:
            old = self.issues[n]["title"]
            self.issues[n]["title"] = title
            self.events.setdefault(n, []).append(
                {
                    "event": "renamed",
                    "rename": {"from": old, "to": title},
                    "actor": {"login": actor},
                    "created_at": self.tick(),
                }
            )
        if body is not None:
            self.issues[n]["body"] = body
            self.edited[n] = {"at": self.tick(), "by": actor}

    def __call__(self, args, cwd=None):
        a = list(args)
        if a[:2] == ["repo", "clone"]:
            p = subprocess.run(
                ["git", "clone", "-q", str(self.origin), a[3]], capture_output=True, text=True
            )
            return p.returncode, p.stdout, p.stderr
        if a[:2] == ["api", "user"]:
            self.calls.append(("GET", "user", None))
            return 0, OWNER + "\n", ""
        if a and a[-1] == "graphql":
            q = json.loads(open(a[a.index("--input") + 1]).read())
            n = int(q["variables"]["n"])
            self.calls.append(("QUERY", f"graphql issue {n}", None))
            e = self.edited.get(n)
            got = (
                {"lastEditedAt": e["at"], "editor": {"login": e["by"]}}
                if e
                else {"lastEditedAt": None, "editor": None}
            )
            return 0, json.dumps({"data": {"repository": {"issue": got}}}), ""
        method = a[a.index("-X") + 1] if "-X" in a else "GET"
        payload = (
            json.loads(Path(a[a.index("--input") + 1]).read_text()) if "--input" in a else None
        )
        path = a[-1].split("?")[0]
        base = f"repos/{self.repo}"
        ok = lambda obj: (0, json.dumps(obj), "")  # noqa: E731
        if path.startswith(f"{base}/issues/") and path.endswith("/events"):
            self.calls.append(("GET", a[-1], None))
            return ok(self.events.get(int(path.split("/")[-2]), []))
        if path in (f"{base}/pulls", f"{base}/issues") and method == "POST":
            self.calls.append((method, a[-1], payload))
            self.next_number += 1
            n = self.next_number
            url = f"https://github.com/{self.repo}/{'pull' if 'pulls' in path else 'issues'}/{n}"
            self.issues[n] = {
                "number": n,
                "title": payload["title"],
                "body": payload["body"],
                "user": {"login": "owner"},
                "author_association": "OWNER",
                "labels": [{"name": x} for x in payload.get("labels") or []],
                "state": "open",
                "created_at": stamp(time.time()),
                "updated_at": stamp(time.time()),
                "html_url": url,
                **(
                    {
                        "pull_request": {},
                        "head": {"ref": payload["head"], "sha": "f00"},
                        "draft": payload.get("draft", False),
                    }
                    if "pulls" in path
                    else {}
                ),
            }
            if "issues" in path:
                for lb in payload.get("labels") or []:
                    self.label(n, lb, OWNER)
            return ok(self.issues[n])
        if path.startswith(f"{base}/pulls/") and path.endswith(("/files", "/reviews")):
            self.calls.append(("GET", a[-1], None))
            if path.endswith("/reviews"):
                return 0, "[]", ""
            files = getattr(self, "pr_files", {})
            return ok([{"filename": f, "changes": c} for f, c in files.items()])
        if path.startswith(f"{base}/git/trees/"):  # main's workflows, and the PR's files
            self.calls.append(("GET", a[-1], None))
            modes = getattr(self, "modes", {})
            on_main = path.endswith(("/main", "/trunk"))
            paths = [*self.files, *([] if on_main else getattr(self, "pr_files", {}))]
            tree = [{"path": f, "type": "blob", "mode": modes.get(f, "100644")} for f in paths]
            return ok({"tree": tree})
        if path.startswith(f"{base}/contents/.github/workflows/"):
            name = path.split("/contents/", 1)[1]
            if name in self.files:
                self.calls.append(("GET", a[-1], None))
                return 0, self.files[name], ""
        if path == f"{base}/tags":
            self.calls.append((method, a[-1], None))
            return ok([{"name": t, "commit": {"sha": "t" + t}} for t in self.tag_list])
        m = re.fullmatch(re.escape(base) + r"/compare/(.+)\.\.\.(.+)", path)
        if m:
            self.calls.append((method, a[-1], None))
            return ok(
                {
                    "commits": [
                        {"sha": s, "commit": {"message": f"commit {s}"}}
                        for s in self.compare_commits
                    ]
                }
            )
        m = re.fullmatch(re.escape(base) + r"/commits/(\w+)/pulls", path)
        if m:
            self.calls.append((method, a[-1], None))
            return ok([self.issues[n] for n in self.commit_prs.get(m.group(1), [])])
        if path == f"{base}/releases" and method == "POST":
            self.calls.append((method, a[-1], payload))
            self.releases.append(payload)
            self.tag_list.append(payload["tag_name"])
            return ok(
                {
                    "tag_name": payload["tag_name"],
                    "html_url": f"https://github.com/{self.repo}/releases/tag/{payload['tag_name']}",
                }
            )
        return super().__call__(args, cwd)

    def comments_of(self, n):
        return [c["body"] for c in self.comments.get(n, [])]

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m not in ("GET", "QUERY")]


def issue(n, title, labels=(), created=None, assignees=(), body="It breaks."):
    return {
        "number": n,
        "title": title,
        "body": body,
        "user": {"login": "reporter"},
        "author_association": "NONE",
        "labels": [{"name": x} for x in labels],
        "created_at": stamp(created or NOW - 86400),
        "updated_at": stamp(NOW - 600),
        "comments": 0,
        "assignees": [{"login": x} for x in assignees],
    }


S5 = issue(14, "A huge numeric id in a URL gives a 500 instead of 404", ["bug", "p2", "go"])


def published(tmp_path, issues, hub=FakeHub, **maintain):
    """A loop with one published build whose repo is ``o/plots`` on the fake GitHub."""
    fake = hub(
        issues, policy_text=policy.render({"status": "maintained"}), origin=make_origin(tmp_path)
    )
    lp = make_loop(
        tmp_path,
        maintain={"revise_command": [sys.executable, "-c", "pass"], "poll_s": 900, **maintain},
    )
    lp._gh = Gh(run=fake)
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper", "one_liner": "Allotment plots."},
        publish={"repo": REPO, "url": f"https://github.com/{REPO}"},
    )
    return lp, fake, i["id"]


def stand_in(monkeypatch, full, origin):
    """The host pushes to the address it builds (``https://github.com/<repo>.git``); here git
    itself maps that address to the stand-in's bare repository, from the environment, the way
    a scratch install does (a tree's own config can never say this: gitcmd refuses it)."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"https://github.com/{full}.git")


# -- the sessions and the test runner -------------------------------------------------------------
class Scripted:
    """A session harness: each call runs the next step on the workspace (``step(ws, prompt)``)
    and costs ``usd``."""

    name = "scripted"

    def __init__(self, *steps, usd=0.4):
        self.steps, self.usd, self.prompts = list(steps), usd, []

    def run(self, prompt, workspace, timeout_s, **kw):
        import uuid

        uuid.UUID(kw["session"]["id"])  # the CLI refuses anything else
        self.prompts.append(prompt)
        ws = Path(workspace)
        step = self.steps.pop(0) if self.steps else (lambda ws, p: None)
        step(ws, prompt)
        return HarnessResult(
            artifact_dir=ws,
            tool_calls=1,
            input_tokens=0,
            output_tokens=0,
            cost_usd=self.usd,
            transcript_path=ws / "transcript.jsonl",
            exit_ok=True,
            session_id=f"s{len(self.prompts)}",
            model="fake",
        )


def note(ws, name, obj):
    (ws / ".repo-factory").mkdir(exist_ok=True)
    (ws / ".repo-factory" / name).write_text(json.dumps(obj))


def names_test(expect="huge id answered 500", test=TEST, existing=True):
    return lambda ws, p: note(
        ws,
        "reproduce.json",
        {"test": test, "existing": existing, "expect": expect, "why": "500, not 404"},
    )


def fixes(ws, p, extra=None):
    app = ws / "app.py"
    app.write_text(app.read_text().replace("        return 500", "        return 404"))
    sec = ws / "SECURITY.md"
    sec.write_text(sec.read_text().replace("| open |", f"| fixed (`{TEST}`) |"))
    ch = ws / "CHANGELOG.md"
    ch.write_text(
        ch.read_text().replace(
            "## Unreleased\n", "## Unreleased\n\n- A huge id is a 404, not a server error.\n"
        )
    )
    note(
        ws,
        "fix.json",
        {
            "what": "status() answers 404 for an id too large for the store.",
            "why": "lookup() raises OverflowError, which became a 500.",
            "agents_md": "unchanged",
            "miss": "security",
            "flow": "F2",
        },
    )
    if extra:
        extra(ws)


def pytest_runner(root, targets):
    """``run_pytest``'s result shape, from plain pytest on a copy (the real one runs in the
    sandbox with a venv; here: this interpreter, no network either way)."""
    from repo_factory.harness.venv import parse_junit

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "a"
        shutil.copytree(
            root, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".repo-factory")
        )
        junit = work / ".j.xml"
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--rootdir",
                str(work),
                f"--junitxml={junit}",
                "-o",
                "junit_family=xunit1",
                *targets,
            ],
            cwd=work,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        tests, errors, details = parse_junit(junit, work) if junit.exists() else ({}, [], {})
    return {"tests": tests, "errors": errors, "details": details, "returncode": p.returncode}


def no_asset(tree):
    return {"why": "release v0.1.0 has no checks asset", "tag": "v0.1.0", "ran": False}


def fix(lp, harness, check=None, number=14, exam=no_asset):
    return revise.Fix(
        lp,
        REPO,
        number,
        harness=harness,
        tests=pytest_runner,
        check=check
        or (lambda root: {"ok": True, "summary": "passed: 1 commands, 0 problems", "output": ""}),
        venv="/nonexistent",
        exam=exam,
    )
