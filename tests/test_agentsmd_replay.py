"""AGENTS.md as the owners wrote it (the owner, 2026-10-17: "Nextread still has the flag Set up
AGENTS.md but the repo actually has one already"). Replays of nextread's and driftwatch's real
AGENTS.md and README (tests/fixtures/agentsmd/, copied from main on 2026-10-17; ``COMMIT`` says
which) through the drift doctor, the poll's AGENTS.md state and the repo-side check:

- nextread's "## Set up, run, test" over a bare fence was "no sh fence": the doctor read one
  spelling of one heading and one fence language;
- driftwatch's "FAILED: 4 commands, 3 problems" was the check running `serve` (it never exits:
  600 s) and `backup <file>` / `restore <file>` (the shell read `<file` as a redirect: exit 2);
- notehatch really has no AGENTS.md, and still says so.

No model, no network: the trees are empty files from ``tree.txt``, as the doctor lays them out."""

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from repo_factory.maintain import agentsmd, revise

FIX = Path(__file__).parent / "fixtures" / "agentsmd"
CHECK = Path(agentsmd.__file__).with_name("templates") / "check_agents_md.py"
HEAD = "01a88265e68515abbefc9ee524b031226de2895f"


def replay(tmp: Path, name: str) -> Path:
    """The repo's tree as the doctor lays it out (``doctor.check_repo``): every path an empty
    file, then the documents it reads."""
    root = tmp / name
    for rel in (FIX / name / "tree.txt").read_text().split():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    for f in ("AGENTS.md", "README.md"):
        if (FIX / name / f).is_file():
            (root / f).write_text((FIX / name / f).read_text())
    return root


class Repo:
    """What ``revise.agents_state`` reads of GitHub: one file and main's commit."""

    full = "iddv/x"

    def __init__(self, text, head=HEAD):
        self.text, self.head = text, head

    def file(self, path, ref):
        return self.text

    def get(self, url):
        return {"sha": self.head}


# -- the replays ---------------------------------------------------------------------------------
def test_nextread_as_it_is_on_main_gives_no_finding(tmp_path):
    root = replay(tmp_path, "nextread")
    text = (root / "AGENTS.md").read_text()
    assert "## Set up, run, test" in text and "\n```\npipx install ." in text  # the real file
    assert [c for c, _ in agentsmd.fence_commands(text)] == [
        "pipx install .",
        "python3 -m nextread --help",
        "python3 -m unittest discover -s tests",
        "python3 -m unittest tests.test_format",
    ]
    assert agentsmd.skipped(text) == [("pipx install .", "installs onto this machine")]
    assert agentsmd.check(root) == []
    a = revise.agents_state(Repo(text), {}, 1.0)
    assert a["state"] == "ok" and revise.agents_rows("iddv/nextread", {"agents_md": a}) == []


def test_driftwatch_as_it_is_on_main_gives_no_finding(tmp_path):
    root = replay(tmp_path, "driftwatch")
    text = (root / "AGENTS.md").read_text()
    assert agentsmd.skipped(text) == [
        ("./bin/driftwatch serve", "starts a server that does not exit"),
        ("./bin/driftwatch backup <file>", "needs a value for <file>"),
        ("./bin/driftwatch restore <file>", "needs a value for <file>"),
    ]
    # what a fix needs is still run: the setup (demo data) and both suites, from ### Tests
    run = [c for c, n in agentsmd.fence_commands(text) if not agentsmd.CHECKER.skip_reason(c, n)]
    assert run == [
        "./bin/driftwatch demo load",
        "./bin/driftwatch test",
        "pip install pytest",
        "python -m pytest tests/acceptance",
    ]
    assert agentsmd.check(root) == []


def test_a_result_of_the_old_check_on_main_is_not_kept():
    """Live 0.3.76: driftwatch's row said the old check's "FAILED: 4 commands, 3 problems" for
    as long as main stayed at 01a8826. A result of an older check is not kept; one of this
    check is, and its row says what is wrong in one line, never "set up"."""
    text = (FIX / "driftwatch" / "AGENTS.md").read_text()
    old = {"sha": HEAD, "ok": False, "summary": "FAILED: 4 commands, 3 problems"}
    assert revise.agents_state(Repo(text), {"agents_main": old}, 1.0)["state"] == "ok"
    now = {
        **old,
        "check": revise.CHECK_VERSION,
        "summary": "FAILED: 4 commands, 1 problems; "
        "not run: `./bin/driftwatch serve` (starts a server that does not exit)",
    }
    a = revise.agents_state(Repo(text), {"agents_main": now}, 1.0)
    assert a["state"] == "broken"
    row = revise.agents_rows("iddv/driftwatch", {"agents_md": a})[0]
    assert row["title"] == a["why"] and "not run: `./bin/driftwatch serve`" in row["title"]
    assert not re.search(r"set up|missing", row["title"], re.I)
    # main moved: the result is about another commit
    assert (
        revise.agents_state(Repo(text, head="f" * 40), {"agents_main": now}, 1.0)["state"] == "ok"
    )


def test_notehatch_has_no_agents_md_and_still_says_so(tmp_path):
    root = replay(tmp_path, "notehatch")
    assert agentsmd.check(root) == ["AGENTS.md is missing"]
    a = revise.agents_state(Repo(None), {}, 1.0)
    assert a["state"] == "missing"
    row = revise.agents_rows("iddv/notehatch", {"agents_md": a})[0]
    assert row["title"].startswith("AGENTS.md is missing: fixes go on")


# -- the check itself, on driftwatch's real AGENTS.md ---------------------------------------------
def stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_check(root: Path, tmp: Path, **env):
    bin_ = tmp / "stubs"
    stub(bin_ / "pip", "exit 0\n")
    stub(bin_ / "python", "exit 0\n")  # `python -m pytest tests/acceptance`
    e = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "AGENTS_MD_TIMEOUT": "20", **env}
    return subprocess.run(
        [sys.executable, str(CHECK), str(root)], capture_output=True, text=True, env=e, timeout=120
    )


@pytest.fixture
def driftwatch(tmp_path):
    root = replay(tmp_path, "driftwatch")
    # a launcher whose server never exits, as the real one: the check must not start it
    stub(
        root / "bin" / "driftwatch",
        'case "$1" in serve) sleep 600;; test) exit "${DW_TEST:-0}";; *) exit 0;; esac\n',
    )
    return root


def test_the_check_runs_what_a_fix_needs_and_says_what_it_left(driftwatch, tmp_path):
    r = run_check(driftwatch, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "skip  ./bin/driftwatch serve  (starts a server that does not exit)" in r.stdout
    assert "skip  ./bin/driftwatch backup <file>  (needs a value for <file>)" in r.stdout
    assert "ok    ./bin/driftwatch test" in r.stdout
    assert r.stdout.strip().splitlines()[-1] == (
        "passed: 4 commands, 0 problems; not run: `./bin/driftwatch serve` (starts a server "
        "that does not exit); `./bin/driftwatch backup <file>` (needs a value for <file>); "
        "`./bin/driftwatch restore <file>` (needs a value for <file>)"
    )


def test_a_command_that_really_fails_is_still_a_finding(driftwatch, tmp_path):
    r = run_check(driftwatch, tmp_path, DW_TEST="1")
    assert r.returncode == 1
    assert "FAIL  `./bin/driftwatch test` exited 1" in r.stdout
    assert revise.problems({"ok": False, "output": r.stdout}) == {"`./bin/driftwatch test`"}


def test_a_fence_of_examples_only_checks_nothing_and_says_so(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "# AGENTS.md\n\n## Setup\n\n```\n./bin/x serve\n./bin/x restore <file>\n```\n"
    )
    r = run_check(tmp_path, tmp_path)
    assert r.returncode == 1 and "none of AGENTS.md's setup commands can run" in r.stdout


def test_the_check_venv_scripts_name_the_venv_they_live_in(tmp_path):
    """Driftwatch's `pip install pytest` exited 127 ("pip: not found") in the check's venv:
    built under ``.tmp-<key>-<pid>`` and renamed, its scripts' ``#!`` named a directory that was
    gone. A venv built so is repaired when next used; the interpreter link is left alone."""
    from repo_factory.harness import venv

    path = tmp_path / "venvs" / "e066957574d9"
    (path / "bin").mkdir(parents=True)
    gone = path.with_name(".tmp-e066957574d9-1018801")
    for name in ("pip", "pytest"):
        stub(path / "bin" / name, "")
        (path / "bin" / name).write_text(f"#!{gone}/bin/python\nimport sys\n")
    (path / "bin" / "python").symlink_to(sys.executable)
    assert venv.relink(path) == ["pip", "pytest"]
    assert (path / "bin" / "pip").read_text() == f"#!{path}/bin/python\nimport sys\n"
    assert os.access(path / "bin" / "pip", os.X_OK)
    assert venv.relink(path) == []  # once is enough


# -- the reader: headings, fences, lines ----------------------------------------------------------
@pytest.mark.parametrize(
    "head",
    [
        "## Setup, run, test",
        "## Set up, run, test",
        "## Setup",
        "## SET UP",
        "## Development",
        "### Running tests",
        "## Tests",
        "## Getting started",
        "## Set up, run and test ##",
    ],
)
@pytest.mark.parametrize("fence", ["```", "```sh", "```bash", "```shell", "~~~shell", "  ```sh"])
def test_a_reasonable_heading_and_fence_are_read(head, fence):
    mark = fence.strip()[:3]
    text = f"# AGENTS.md\n\nIntro.\n\n{head}\n\n{fence}\n./x.sh test   # the suite\n{mark}\n"
    assert agentsmd.fence_commands(text) == [("./x.sh test", "the suite")]
    assert agentsmd.CHECKER.fence_lines(text) == agentsmd.fence_commands(text)


def test_what_is_not_a_command_is_not_read():
    text = (
        "# AGENTS.md\n\n## Usage\n\n```sh\n./x.sh other\n```\n\n## Setup\n\n```toml\n"
        "a = 1\n```\n\n```console\n$ ./x.sh test \\\n    --fast\nOK (3 tests)\n```\n\n"
        "## Next\n\n```sh\n./x.sh after\n```\n"
    )
    assert agentsmd.fence_commands(text) == [("./x.sh test --fast", "")]
    assert agentsmd.fence_commands("# A\n\n## Setup\n\nNo fence.\n") == []
    assert agentsmd.CHECKER.__file__ == str(CHECK)  # one reader: the repo-side check's


@pytest.mark.parametrize(
    "cmd,note,why",
    [
        ("./bin/x serve", "", "starts a server that does not exit"),
        ("./bin/x start -d", "", "starts a server that does not exit"),
        ("./bin/x run", "listens on http://127.0.0.1:8080", "starts a server that does not exit"),
        ("python -m http.server 8000", "", "starts a server that does not exit"),
        ("./bin/x serve --help", "", None),
        (
            "./x.sh",
            "serve: http://127.0.0.1:8099/ (port: X_PORT)",
            None,
        ),  # repo-factory's own: waited for
        ("./bin/x backup <file>", "", "needs a value for <file>"),
        ("./bin/x import /path/to/list.csv", "", "needs a value for /path/to"),
        ("sort < in.txt > out.txt", "", None),
        ("sudo make install", "", "needs root"),
        ("pipx install .", "", "installs onto this machine"),
        ("python3 -m pip install --user .", "", "installs onto this machine"),
        ("python -m pip install pytest", "", None),
        ("npm run test", "", None),
        ("python3 -m unittest discover -s tests", "", None),
        ("NEXTREAD_TODAY=2026-10-11 python3 -m nextread next", "", None),
    ],
)
def test_only_examples_are_left_unrun(cmd, note, why):
    assert agentsmd.CHECKER.skip_reason(cmd, note) == why


def test_the_architecture_section_is_read_as_written():
    nextread = (FIX / "nextread" / "AGENTS.md").read_text()
    assert "nextread/store.py" in agentsmd.table_paths(nextread)  # "## Architecture (`nextread/`)"
    drift = agentsmd.table_paths((FIX / "driftwatch" / "AGENTS.md").read_text())
    assert {"bin/driftwatch", "SCOPE.md", "CHANGELOG.md", "tests/acceptance/"} <= set(drift)
    assert agentsmd.table_paths("# A\n\n## Setup\n\n```\nx\n```\n") is None


# -- the words: AGENTS.md is advice -------------------------------------------------------------
UI = Path(agentsmd.__file__).parents[1]
CLAIM = re.compile(
    r"needs? (?:it|AGENTS\.md)\b[^.\n]{0,40}\b(?:run|start)s? (?:a |the )?fix"
    r"|before it can run fixes|the file repo-factory needs|held fixes|fixes are held"
    r"|repo-factory needs AGENTS\.md",
    re.I,
)


def test_no_ui_text_says_agents_md_is_needed_to_run_fixes():
    files = [
        p
        for p in (UI / "ui" / "web" / "src").rglob("*")
        if p.suffix in (".svelte", ".ts") and not p.name.endswith(".test.ts")
    ]
    files += [UI / "registry.py", UI / "ui" / "api.py", UI / "ui" / "help.md"]
    bad = [
        f"{p.relative_to(UI)}:{n}: {m.group(0)}"
        for p in files
        for n, ln in enumerate(p.read_text().splitlines(), 1)
        for m in [CLAIM.search(ln)]
        if m
    ]
    assert bad == []
