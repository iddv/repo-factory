"""The maintenance package's static half (MAINTAIN.md §11, M1): templates, labels, the policy
file, AGENTS.md from a product tree, its drift doctor, check_agents_md.py, and the backfill
end to end against a fake ``gh`` and a local bare repo. No model, no network."""

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from repo_factory.gh import Gh
from repo_factory.maintain import TEMPLATES, TemplateError, agentsmd, backfill, fill, labels, policy
from repo_factory.maintain import render as render_template

pytestmark = pytest.mark.usefixtures("maintenance_on")

SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


# -- fixtures ----------------------------------------------------------------------------------
class FakeGh:
    """Just enough of ``gh`` for labels, settings, the contents API, clone and PRs; every
    call is recorded."""

    def __init__(self, origin: Path | None = None, policy_text: str | None = None):
        self.origin, self.policy_text = origin, policy_text
        self.calls, self.labels, self.prs = [], {}, []
        self.settings, self.alerts = {}, False
        self.private = True

    def __call__(self, args, cwd=None):
        self.calls.append(list(args))
        a = list(args)
        if a[:2] == ["repo", "view"]:
            return 0, ("true" if self.private else "false") + "\n", ""
        if a[:2] == ["label", "list"]:
            return 0, json.dumps(list(self.labels.values())), ""
        if a[:2] == ["label", "create"]:
            name = a[2]
            self.labels[name] = {
                "name": name,
                "color": a[a.index("--color") + 1],
                "description": a[a.index("--description") + 1],
            }
            return 0, "", ""
        if a[:2] == ["label", "delete"]:
            self.labels.pop(a[2], None)
            return 0, "", ""
        if a[:3] == ["api", "-X", "PATCH"] and "/pulls/" in a[3]:
            n = int(a[3].rsplit("/", 1)[-1])
            self.prs[n - 1]["body"] = Path(a[a.index("-F") + 1].split("=@", 1)[1]).read_text()
            return 0, f"{n}\n", ""
        if a[:3] == ["api", "-X", "PATCH"]:
            self.settings["delete_branch_on_merge"] = True
            return 0, "true\n", ""
        if a[:3] == ["api", "-X", "PUT"] and a[3].endswith("/vulnerability-alerts"):
            self.alerts = True
            return 0, "", ""
        if a[0] == "api" and a[-1].endswith("/vulnerability-alerts"):
            return (0, "", "") if self.alerts else (1, "", "HTTP 404: Not Found")
        if a[0] == "api" and "contents/.github/repo-factory.yml" in a[-1]:
            if self.policy_text is None:
                return 1, "", "gh: Not Found (HTTP 404)"
            return 0, self.policy_text, ""
        if a[:2] == ["repo", "clone"]:
            p = subprocess.run(
                ["git", "clone", "-q", str(self.origin), a[3]], capture_output=True, text=True
            )
            return p.returncode, p.stdout, p.stderr
        if a[:2] == ["pr", "list"]:
            return 0, json.dumps([p for p in self.prs if p["state"] == "open"]), ""
        if a[:2] == ["pr", "create"]:
            n = len(self.prs) + 1
            body = Path(a[a.index("--body-file") + 1]).read_text()
            self.prs.append(
                {
                    "number": n,
                    "url": f"https://github.com/o/r/pull/{n}",
                    "state": "open",
                    "body": body,
                    "labels": [a[a.index("--label") + 1]],
                }
            )
            return 0, f"https://github.com/o/r/pull/{n}\n", ""
        return 1, "", f"fake gh: unexpected {a[:3]}"


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip("\n"))
    return p


SERVER = '''
"""Tiny server for the fixture: answers on 127.0.0.1 at TOYBOX_PORT."""
import http.server, os
http.server.HTTPServer(("127.0.0.1", int(os.environ.get("TOYBOX_PORT", "8099"))),
                       http.server.SimpleHTTPRequestHandler).serve_forever()
'''

OPEN_MARK = """
# Known open item: expected to fail until it is fixed (tests/acceptance/README.md).
import pytest as _pytest_open  # noqa: E402
_marks = globals().get('pytestmark', [])
pytestmark = (list(_marks) if isinstance(_marks, (list, tuple)) else [_marks]) + [
    _pytest_open.mark.xfail(strict=False, reason='Known open issue: the flag')]
"""


def product(root: Path) -> Path:
    """A product laid out like the published ones: README with *At a glance*, install, tests
    and layout sections, SCOPE.md with flows, ASSUMPTIONS.md settings, an acceptance suite
    with one known open item, a launcher and a package."""
    _w(
        root / "README.md",
        """
        # Toybox

        Toybox lends toys to families.

        ## At a glance

        - **What it is:** A self-hosted toy library for a small club
        - **Who it's for:** Volunteers who lend toys.

        It needs Python 3.10 or newer.

        ## Install and start (one command)

        ```sh
        git clone <this repository> toybox && cd toybox && ./toybox.sh
        ```

        It starts on http://127.0.0.1:8099/.

        ## Commands

        | Command | What it does |
        |---|---|
        | `./toybox.sh demo` | Load demo data |
        | `./toybox.sh backup [-o FILE]` | Write a backup |
        | `./toybox.sh restore FILE` | Restore a backup |

        ## Configuration

        | Variable | Default | Meaning |
        |---|---|---|
        | `TOYBOX_PORT` | `8099` | Listen port |
        | `TOYBOX_DATA_DIR` | `./data` | All data |

        Loans are append-only, and nothing is ever deleted. Amounts are integer cents.
        The schema is versioned; migrations run at start.

        ## Tests

        ```sh
        ./toybox.sh test
        python -m pytest tests/acceptance
        ```

        ## Layout

        - `toybox/web.py`: HTTP pages
        """,
    )
    _w(
        root / "SCOPE.md",
        """
        # Scope: Toybox

        Toybox is a lending register for a toy library. It keeps loans. It sends nothing.

        ## Core flows

        ### F1: Install and first run
        ### F2: Lend a toy
        ### F3: Return a toy
        """,
    )
    _w(
        root / "ASSUMPTIONS.md",
        """
        # Assumptions

        ## Settings

        | key | values | default | scope | settles |
        |---|---|---|---|---|
        | `loans.renew` | allowed, refused | `allowed` | global | May a loan be renewed? |

        ### Not available yet

        | key | values | proposed default | settles |
        |---|---|---|---|
        | `loans.fines` | on, off | `off` | Fines? |

        ## Decisions
        """,
    )
    _w(root / "NOTICE", "Toybox\n\nThis software was generated by an autonomous software build.\n")
    _w(
        root / "toybox.sh",
        '#!/bin/sh\n# Toybox launcher\ncd "$(dirname "$0")"\n'
        'if [ "$1" = test ]; then exec python3 -m unittest -q tests.test_toybox; fi\n'
        "exec python3 serve.py\n",
    ).chmod(0o755)
    _w(root / "serve.py", SERVER)
    _w(root / "toybox" / "__init__.py", '"""Toybox."""\n')
    _w(root / "toybox" / "core.py", '"""Loans and returns: the domain rules."""\n')
    _w(root / "toybox" / "web.py", "X = 1\n")
    _w(root / "toybox.conf.example", "port=8099\n")
    _w(root / "tests" / "__init__.py", "")
    _w(
        root / "tests" / "test_toybox.py",
        '"""Unit tests for the loan rules."""\nimport unittest\n\n\n'
        "class T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
    )
    _w(
        root / "tests" / "acceptance" / "README.md",
        """
        # Acceptance tests

        ## Known open items

        - `test_flag.py`: Known open issue: the flag
        """,
    )
    _w(root / "tests" / "acceptance" / "test_lend.py", "def test_lend():\n    assert True\n")
    _w(
        root / "tests" / "acceptance" / "test_flag.py",
        '"""The flag file exists."""\nimport os\n\n\ndef test_flag():\n'
        '    assert os.path.exists(os.path.join(os.path.dirname(__file__), "FLAG"))\n\n'
        + OPEN_MARK,
    )
    _w(root / ".github" / "workflows" / "test.yml", "name: test\non: push\n")
    return root


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def git_env(monkeypatch):
    for k, v in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@t"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@t"),
    ):
        monkeypatch.setenv(k, v)


@pytest.fixture
def origin(tmp_path, git_env):
    """A bare repo holding the fixture product on ``main``, tagged v0.1.0."""
    src = product(tmp_path / "src")
    git("init", "-q", "-b", "main", cwd=src)
    git("add", "-A", cwd=src)
    git("commit", "-q", "-m", "Toybox 0.1.0", cwd=src)
    git("tag", "v0.1.0", cwd=src)
    bare = tmp_path / "origin.git"
    git("clone", "-q", "--bare", str(src), str(bare), cwd=tmp_path)
    return bare


def values(root: Path) -> dict:
    return agentsmd.values_from_tree(root, "o/toybox")


# -- templates ---------------------------------------------------------------------------------
def test_every_template_renders_and_leaves_no_placeholder(tmp_path):
    v = values(product(tmp_path / "p"))
    names = sorted(p.name for p in TEMPLATES.iterdir())
    assert {
        "AGENTS.md.tmpl",
        "CLAUDE.md",
        "ci.yml",
        "labels.yml",
        "dependabot.yml",
        "bug.yml",
        "feature.yml",
        "config.yml",
        "pull_request_template.md",
        "CHANGELOG.md.tmpl",
        "check_agents_md.py",
        "sync_labels.py",
        "repo-factory.yml",
    } <= set(names)
    for n in names:
        text = policy.render() if n == "repo-factory.yml" else render_template(n, v)
        assert not re.search(r"(?<!\$)\{\{", text), n
    with pytest.raises(TemplateError):
        fill("{{nope}}", {})


def test_templates_are_neutral():
    """Nothing private reaches a product: no home paths, host names or the owner's account."""
    for p in TEMPLATES.iterdir():
        text = p.read_text()
        for bad in ("self-hosted",):
            assert bad not in text, (p.name, bad)
        hits = [k for ln in text.splitlines() for k in _public_words().line_hits(ln)]
        assert hits == [], (p.name, hits)


def test_ci_workflow_is_pinned_read_only_and_hosted(tmp_path):
    v = values(product(tmp_path / "p"))
    for name in ("ci.yml", "labels.yml"):
        text = render_template(name, v)
        doc = yaml.safe_load(text)
        assert doc["permissions"] in ({"contents": "read"}, {"issues": "read"})
        uses = re.findall(r"uses:\s*(\S+)", text)
        assert uses and all(SHA.match(u) for u in uses), uses
        assert "self-hosted" not in text and "pull_request_target" not in text
        for job in doc["jobs"].values():
            assert job["runs-on"] == "ubuntu-24.04"
            assert job.get("timeout-minutes")
    ci = yaml.safe_load(render_template("ci.yml", v))
    assert ci["permissions"] == {"contents": "read"}
    assert ci["jobs"]["test"]["strategy"]["matrix"]["python-version"] == ["3.10", "3.12"]
    assert "./toybox.sh test" in json.dumps(ci["jobs"]["test"]["steps"])


def test_dependabot_and_forms_parse(tmp_path):
    v = values(product(tmp_path / "p"))
    dep = yaml.safe_load(render_template("dependabot.yml", v))
    assert [u["package-ecosystem"] for u in dep["updates"]] == ["github-actions"]
    assert yaml.safe_load(render_template("bug.yml", v))["labels"] == ["triage", "bug"]
    assert yaml.safe_load(render_template("feature.yml", v))["labels"] == ["triage", "feature"]
    cfg = yaml.safe_load(render_template("config.yml", v))
    assert cfg["blank_issues_enabled"] is False
    assert cfg["contact_links"][0]["url"] == "https://github.com/o/toybox/security/advisories/new"
    (tmp_path / "p" / "requirements.txt").write_text("")
    dep = yaml.safe_load(render_template("dependabot.yml", values(tmp_path / "p")))
    assert [u["package-ecosystem"] for u in dep["updates"]] == ["github-actions", "pip"]


# -- labels ------------------------------------------------------------------------------------
def test_the_scheme_has_22_labels_in_their_groups():
    assert len(labels.LABELS) == len(set(labels.NAMES)) == 22
    assert labels.KINDS == ["bug", "feature", "question", "security", "dependency", "docs", "task"]
    assert labels.PRIORITIES == ["p1", "p2", "p3"]
    assert {
        "go",
        "needs-you",
        "claim:repo-factory",
        "claim:agent",
        "lesson",
        "repo-factory",
    } <= set(labels.NAMES)
    assert all(re.fullmatch(r"[0-9a-f]{6}", c) for _, c, _, _ in labels.LABELS)


def test_labels_yaml_is_yaml_and_the_stdlib_reader_agrees(tmp_path):
    text = labels.labels_yaml()
    doc = yaml.safe_load(text)
    assert [x["name"] for x in doc["labels"]] == labels.NAMES
    assert labels.parse_labels_yaml(text) == doc["labels"]
    ns: dict = {"__file__": str(TEMPLATES / "sync_labels.py"), "__name__": "sync_labels"}
    exec(compile((TEMPLATES / "sync_labels.py").read_text(), "sync_labels.py", "exec"), ns)
    p = tmp_path / "labels.yml"
    p.write_text(text)
    assert ns["read_labels"](str(p)) == doc["labels"]


def test_apply_creates_each_label_once_and_is_a_no_op_the_second_time():
    fake = FakeGh()
    fake.labels["enhancement"] = {"name": "enhancement", "color": "a2eeef", "description": "x"}
    fake.labels["bug"] = {
        "name": "bug",
        "color": "d73a4a",
        "description": "Something isn't working",
    }
    gh = Gh(run=fake)
    res = labels.apply(gh, "o/r")
    creates = [c for c in fake.calls if c[:2] == ["label", "create"]]
    assert len(creates) == 22 and all(c[-1] == "--force" and "--repo" in c for c in creates)
    assert res["updated"] == ["bug"] and len(res["created"]) == 21
    assert res["extra"] == ["enhancement"] and "enhancement" in fake.labels  # never pruned
    fake.calls.clear()
    res = labels.apply(gh, "o/r")
    assert [c for c in fake.calls if c[:2] != ["label", "list"]] == []
    assert len(res["unchanged"]) == 22
    assert labels.prune_commands("o/r", res["extra"]) == [
        'gh label delete "enhancement" --repo o/r --yes'
    ]


def test_prune_only_when_asked_and_dry_run_changes_nothing():
    fake = FakeGh()
    fake.labels["invalid"] = {"name": "invalid", "color": "e4e669", "description": ""}
    gh = Gh(run=fake)
    res = labels.apply(gh, "o/r", prune=True, dry_run=True)
    assert res["pruned"] == ["invalid"] and fake.labels.keys() == {"invalid"}
    labels.apply(gh, "o/r", prune=True)
    assert "invalid" not in fake.labels and len(fake.labels) == 22


# -- the policy file ---------------------------------------------------------------------------
def test_the_documented_file_parses_to_the_defaults():
    text = policy.render()
    assert policy.parse(text) == policy.DEFAULTS
    p = policy.parse(policy.render({"status": "as-is", "revise": {"auto_go": "patch"}}))
    assert p["status"] == "as-is" and p["revise"]["auto_go"] == "patch"
    assert p["revise"]["kinds"] == ["bug", "security", "docs", "dependency"]


def test_yaml_off_reads_as_off():
    p = policy.parse(
        "repo-factory: 1\nrevise:\n  trigger: off\n  auto_go: off\nrelease:\n  on_merge: off\n"
    )
    assert (p["revise"]["trigger"], p["revise"]["auto_go"], p["release"]["on_merge"]) == (
        "off",
        "off",
        "off",
    )


@pytest.mark.parametrize(
    "text, why",
    [
        ("repo-factory: 1\nmerge:\n  auto_merge: true\n", "unknown key merge.auto_merge"),
        ("repo-factory: 1\nstatus: retired\n", "status must be one of"),
        (
            "repo-factory: 1\nmerge:\n  factory_patch: always\n",
            "merge.factory_patch must be one of",
        ),
        ("repo-factory: 1\nrevise:\n  kinds: [bug, chores]\n", "revise.kinds"),
        ("repo-factory: 1\nrevise:\n  max_usd: -3\n", "revise.max_usd"),
        ("repo-factory: 2\n", "schema version 2"),
        ("[1, 2]\n", "must be a mapping"),
        ("repo-factory: [\n", "not valid YAML"),
    ],
)
def test_parse_rejects_typos_and_bad_values(text, why):
    with pytest.raises(policy.PolicyError, match=re.escape(why)):
        policy.parse(text)


def test_a_missing_file_means_as_is_and_spend_nothing():
    for p in (policy.parse(None), policy.parse("")):
        assert p["status"] == "as-is"
        assert p["triage"]["enabled"] is True
        assert p["revise"]["trigger"] == "off"
        assert p["merge"]["factory_patch"] == "never" and p["merge"]["dependabot"] == "never"
        assert p["release"]["on_merge"] == "off"


def test_load_reads_the_repo_and_treats_404_as_missing():
    assert policy.load(Gh(run=FakeGh()), "o/r")["status"] == "as-is"
    fake = FakeGh(policy_text="repo-factory: 1\nstatus: archived\n")
    assert policy.load(Gh(run=fake), "o/r")["status"] == "archived"
    assert fake.calls[0][-1] == "repos/o/r/contents/.github/repo-factory.yml"


# -- AGENTS.md ---------------------------------------------------------------------------------
def test_values_from_tree_reads_commands_flows_settings_and_open_items(tmp_path):
    v = values(product(tmp_path / "p"))
    assert (
        v["product"] == "Toybox" and v["one_liner"] == "A self-hosted toy library for a small club."
    )
    assert v["who"] == "Volunteers who lend toys."
    assert (
        v["python_min"] == "3.10"
        and v["port"] == "8099"
        and v["port_note"] == " (port: TOYBOX_PORT)"
    )
    assert v["install_command"] == "./toybox.sh" and v["unit_test_command"] == "./toybox.sh test"
    assert v["demo_command"] == "./toybox.sh demo"
    assert v["backup_command"] == "./toybox.sh backup [-o FILE]"
    assert v["restore_command"] == "./toybox.sh restore FILE"
    assert v["conf_file"] == "toybox.conf" and v["data_dir"] == "./data"
    assert v["flow_range"] == "F1–F3" and [f for f, _ in v["flows"]] == ["F1", "F2", "F3"]
    assert v["open_items"] == [{"file": "test_flag.py", "why": "Known open issue: the flag"}]
    assert "| `loans.renew` | allowed, refused | `allowed` |" in v["operator_settings"]
    assert (
        "not in this version" in v["operator_settings"]
        and "`loans.fines`" in v["operator_settings"]
    )
    assert v["append_only_note"] == "Loans are append-only, and nothing is ever deleted."
    assert v["money_convention"].startswith("- Money is integer cents")
    assert v["first_version"] == "0.1.0" and v["does"] == agentsmd.DOES  # never SCOPE.md's
    rows = dict(v["architecture"])
    assert rows["toybox/core.py"] == "Loans and returns: the domain rules."
    assert rows["toybox/web.py"] == "HTTP pages."  # the README's layout list
    assert rows["toybox.sh"].startswith("The launcher the README uses: `./toybox.sh`")
    assert "toybox/__init__.py" not in rows and "tests/acceptance/" in rows


BELIEFLINE_README = """# Beliefline

An append-only memory log you run next to your own AI agents.

Python 3.9+ standard library only: no dependencies to install.

## Install and start (one command)

```
./bin/beliefline start -d
```

This starts the service in the background on `127.0.0.1:7700`. Then:

```
./bin/beliefline status      # service up
./bin/beliefline demo load   # optional: 2 agents
```

Optionally `pip install .` puts a `beliefline` command on your PATH (`beliefline start -d`).

Run the tests: `python3 -m unittest discover -s tests -v`

## Acceptance tests

End-to-end checks of the product's behaviour are in `tests/acceptance/` (they need pytest):

    python -m pytest tests/acceptance
"""


def test_an_inline_launcher_is_read_when_there_is_no_fence():
    readme = "# X\n\n## Install and run\n\nStart it with `./bin/x serve --port 8000`.\n"
    assert agentsmd.launcher(readme) == "./bin/x serve --port 8000"


def test_a_describer_fills_modules_without_a_docstring(tmp_path):
    root = product(tmp_path / "p")
    (root / "README.md").write_text(
        (root / "README.md").read_text().replace("- `toybox/web.py`: HTTP pages", "")
    )
    asked = {}

    def describe(mods):
        asked.update(mods)
        return {"toybox/web.py": "The web pages.", "other.py": "ignored"}

    v = agentsmd.values_from_tree(root, "o/toybox", describe)
    assert list(asked) == ["toybox/web.py"] and asked["toybox/web.py"] == ["X = 1"]
    assert dict(v["architecture"])["toybox/web.py"] == "The web pages."


# -- check_agents_md.py ------------------------------------------------------------------------
def run_checker(root: Path):
    return subprocess.run(
        [sys.executable, str(root / ".github" / "scripts" / "check_agents_md.py")],
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture
def checked_tree(tmp_path):
    root = product(tmp_path / "p")
    backfill.write_files(root, values(root))
    # the fence's acceptance line installs pytest from the network; here it runs the suite only
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text().replace(
            "python -m pip install pytest && python -m pytest tests/acceptance",
            f"{sys.executable} -m pytest tests/acceptance -q -p no:cacheprovider",
        )
    )
    return root


def test_check_agents_md_runs_the_fence_and_serves_on_a_free_port(checked_tree):
    r = run_checker(checked_tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"ok    \./toybox\.sh  \(answered on 127\.0\.0\.1:\d+\)", r.stdout)
    assert "ok    ./toybox.sh test" in r.stdout
    assert "passed: 3 commands, 0 problems" in r.stdout


def test_check_agents_md_fails_on_a_missing_path(checked_tree):
    (checked_tree / "toybox" / "core.py").unlink()
    r = run_checker(checked_tree)
    assert r.returncode == 1
    assert "the architecture section names `toybox/core.py`, which does not exist" in r.stdout


def test_check_agents_md_fails_on_an_xpass_that_keeps_its_mark(checked_tree):
    (checked_tree / "tests" / "acceptance" / "FLAG").write_text("fixed")
    r = run_checker(checked_tree)
    assert r.returncode == 1
    assert (
        "tests/acceptance/test_flag.py now passes but still carries its open-item mark" in r.stdout
    )
    flag = checked_tree / "tests" / "acceptance" / "test_flag.py"
    flag.write_text(flag.read_text().split("# Known open item")[0])  # the mark comes off
    assert run_checker(checked_tree).returncode == 0


def _listening(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_a_daemon_that_leaves_the_process_group_is_stopped_too(checked_tree):
    """Beliefline's quick start is `start -d`: the service detaches into its own session, so
    stopping the line's process group left it running (and the next check found it "already
    running"). Everything the line started is stopped, whatever its group."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (checked_tree / "toybox.sh").write_text(
        '#!/bin/sh\ncd "$(dirname "$0")"\n'
        'if [ "$1" = test ]; then exec python3 -m unittest -q tests.test_toybox; fi\n'
        f"TOYBOX_PORT={port} setsid python3 serve.py >/dev/null 2>&1 < /dev/null &\n"
    )
    agents = checked_tree / "AGENTS.md"
    agents.write_text(
        agents.read_text()
        .replace(" (port: TOYBOX_PORT)", "")
        .replace("127.0.0.1:8099", f"127.0.0.1:{port}")
    )
    r = run_checker(checked_tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"answered on 127.0.0.1:{port}" in r.stdout
    assert not _listening(port)


def test_a_detached_daemon_is_waited_for_until_it_answers(checked_tree):
    """`start -d` exits 0 at once, before its daemon listens: the check waits while what the
    line started still runs (a slow runner made the test above fail at that exit)."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (checked_tree / "toybox.sh").write_text(
        '#!/bin/sh\ncd "$(dirname "$0")"\n'
        'if [ "$1" = test ]; then exec python3 -m unittest -q tests.test_toybox; fi\n'
        f'TOYBOX_PORT={port} setsid sh -c "sleep 2; exec python3 serve.py" >/dev/null 2>&1 < /dev/null &\n'
    )
    agents = checked_tree / "AGENTS.md"
    agents.write_text(
        agents.read_text()
        .replace(" (port: TOYBOX_PORT)", "")
        .replace("127.0.0.1:8099", f"127.0.0.1:{port}")
    )
    r = run_checker(checked_tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"answered on 127.0.0.1:{port}" in r.stdout
    assert not _listening(port)


def test_check_agents_md_fails_on_a_failing_command(checked_tree):
    agents = checked_tree / "AGENTS.md"
    agents.write_text(agents.read_text().replace("./toybox.sh test   #", "./toybox.sh nosuch   #"))
    (checked_tree / "toybox.sh").write_text(
        '#!/bin/sh\n[ "$1" = nosuch ] && exit 3\nexec python3 serve.py\n'
    )
    r = run_checker(checked_tree)
    assert r.returncode == 1 and "`./toybox.sh nosuch` exited 3" in r.stdout


def test_cli_prints_the_labels_file_and_the_policy_template(capsys):
    from repo_factory.cli import main

    main(["maintain", "labels", "--print"])
    assert capsys.readouterr().out == labels.labels_yaml()
    main(["maintain", "policy", "--template"])
    assert policy.parse(capsys.readouterr().out) == policy.DEFAULTS


def _public_words():
    """The repository's own scanner of private names and paths (scripts/ci/public_words.py)."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "public_words", root / "scripts" / "ci" / "public_words.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
