"""The README's quick start runs one start command (``v5/commands.py``, ``v5/cmdrunner.py``):
while the server a quick-start step started still serves, a later start command is how the
README says to start it again, not a second server; a step offered with "or" runs only when
the one before it didn't work; and an example that takes a data file of the user's own is not
a failure of the README. Every flow's gate reads these.

The real cases (track LEAN2):
- **Plotkeeper (LEAN's lean run):** "Stop Plotkeeper with Ctrl+C. Start it again later with:
  ``npm start``" ran while ``sh install.sh`` still served, hit the product's own "already
  running" guard, and held the build (a false hold: the tester got 11/11 from the same
  README).
- **Nextread r1 (EV8):** ``nextread import tabs.txt  # bulk capture: one title or URL per
  line`` failed with "cannot read tabs.txt", a reading list the user brings; EV8's quick-start
  check counted it red."""

from pathlib import Path

import pytest

from repo_factory.v5 import cmdrunner, commands

FIX = Path(__file__).parent / "fixtures" / "quickstart"
SANDBOX = pytest.mark.skipif(not commands.available(), reason="needs bwrap")


def venv():
    from repo_factory.harness.venv import ensure_venv

    return ensure_venv([])


def tree(root: Path, readme: str, files: dict) -> Path:
    root.mkdir(parents=True)
    (root / "README.md").write_text(readme)
    for rel, text in files.items():
        (root / rel).write_text(text)
        (root / rel).chmod(0o755)
    return root


def steps(root: Path) -> list[dict]:
    return [commands._step(e) for e in commands.extract(root)["quick"] if not e["skip"]]


# -- reading the real READMEs ----------------------------------------------------------------
def test_plotkeeper_s_quick_start_has_one_start_command_and_a_restart_line(tmp_path):
    (tmp_path / "README.md").write_text((FIX / "plotkeeper-lean-README.md").read_text())
    got = {s["cmd"]: s for s in steps(tmp_path)}
    assert list(got) == ["sh install.sh", "npm run demo", "npm start"]
    # install.sh serves (it execs the server) but says so by running, not by its verb; the
    # demo and the backup run once; ``npm start`` is the start command
    assert [got[c]["start"] for c in got] == [False, False, True]
    assert not any(s["alt"] for s in got.values())


def test_nextread_r1_s_quick_start_reads_as_written(tmp_path):
    (tmp_path / "README.md").write_text((FIX / "nextread-r1-README.md").read_text())
    ex = commands.extract(tmp_path)
    assert [e["cmd"] for e in ex["quick"]][:4] == [
        "pipx install .",
        "nextread",
        'nextread add "Dune" --author "Frank Herbert" --len 612p --prio 1',
        "nextread import tabs.txt",
    ]
    assert not any(commands._step(e)["start"] for e in ex["quick"])
    # the file the user brings, as the runner sees it in a fresh copy
    assert cmdrunner.inputs_missing("nextread import tabs.txt", str(tmp_path)) == ["tabs.txt"]


def test_which_commands_start_the_service():
    for cmd in (
        "npm start",
        "npm run dev",
        "yarn start",
        "pnpm run serve",
        "node bin/plotkeeper.js start",
        "./driftwatch.sh serve --port 8080",
        "python -m frontdesk serve",
        "flask run",
        "uvicorn app:app",
        "docker compose up",
    ):
        assert commands.START.search(cmd), cmd
    for cmd in (
        "npm run demo",
        "npm run backup",
        "sh install.sh",
        "npm test",
        "nextread add Dune",
        "python -m frontdesk migrate",
        "./run-once.sh",
    ):
        assert not commands.START.search(cmd), cmd


def test_a_comment_or_prose_or_marks_the_next_command_an_alternative(tmp_path):
    (tmp_path / "README.md").write_text(
        "# X\n\n## Quick start\n\n```sh\n./x.sh setup\n# or, without the script:\n"
        "python3 -m x setup\n./x.sh hello\n```\n\nOr, by hand:\n\n```sh\n./x.sh by-hand\n```\n"
        "\nTo try it with sample data instead, run:\n\n```sh\n./x.sh demo\n```\n"
    )
    alt = {s["cmd"]: s["alt"] for s in steps(tmp_path)}
    assert alt == {
        "./x.sh setup": False,
        "python3 -m x setup": True,
        "./x.sh hello": False,
        "./x.sh by-hand": True,
        "./x.sh demo": False,
    }


def test_a_user_s_own_file_is_a_data_file_not_a_script(tmp_path):
    (tmp_path / "there.csv").write_text("a\n")
    assert cmdrunner.inputs_missing("x import list.opml && x list", str(tmp_path)) == ["list.opml"]
    assert cmdrunner.inputs_missing("x import there.csv", str(tmp_path)) == []
    # a missing script or program is the README's own bug
    for cmd in ("node app.js", "python3 serve.py", "sh install.sh", "x import ../a/b.txt"):
        assert cmdrunner.inputs_missing(cmd, str(tmp_path)) == [], cmd


# -- running them ------------------------------------------------------------------------------
# a stand-in for Plotkeeper with its README's quick start (``npm`` -> ``./pk.sh``): a server
# with an "already running" guard on its data folder, a demo and a backup that run once
PK = r"""import http.server, os, sys
os.makedirs("data", exist_ok=True)
pid = os.path.join("data", "pid")
cmd = sys.argv[1]
if cmd == "start":
    if os.path.exists(pid):
        try:
            os.kill(int(open(pid).read()), 0)
            print("Plotkeeper already seems to be running with this data. Stop it first.")
            sys.exit(1)
        except OSError:
            pass
    open(pid, "w").write(str(os.getpid()))
    print("Plotkeeper is running at http://127.0.0.1:4680/", flush=True)
    http.server.HTTPServer(("127.0.0.1", 4680), http.server.SimpleHTTPRequestHandler) \
        .serve_forever()
print(cmd, "done")
"""
PK_FILES = {
    "pk.py": PK,
    "install.sh": "#!/bin/sh\nset -e\npython pk.py migrate\nexec python pk.py start\n",
    "pk.sh": '#!/bin/sh\nexec python pk.py "$@"\n',
}


def plotkeeper_readme() -> str:
    return (FIX / "plotkeeper-lean-README.md").read_text().replace(
        "npm run demo", "./pk.sh demo"
    ).replace("npm start", "./pk.sh start") + "\n## Backups\n\n```sh\n./pk.sh backup\n```\n"


@SANDBOX
def test_plotkeeper_s_restart_line_is_not_run_beside_the_running_server(tmp_path):
    root = tree(tmp_path / "pk", plotkeeper_readme(), PK_FILES)
    # ``sh install.sh`` names no start verb: it is waited out for a step's time, then taken
    # for the server it is
    res = commands.run(root, venv(), ("quick",), timeouts={"step_s": 6})
    st = {r["cmd"]: r for r in res["quick"]}
    assert st["sh install.sh"]["status"] == "serving"
    assert st["./pk.sh demo"]["status"] == "ok"
    assert st["./pk.sh start"]["status"] == "skipped" and st["./pk.sh start"]["alternative"]
    assert "sh install.sh" in st["./pk.sh start"]["why"]
    assert st["./pk.sh backup"]["status"] == "ok"
    it = commands.items(res, tests=False, restart=False)["commands"]
    assert it["status"] == "green", it
    assert "another way to start" in it["title"] + it["why"]


@SANDBOX
def test_a_start_command_runs_when_nothing_serves(tmp_path):
    # the quick start's own start command, with nothing started before it: it runs
    readme = "# PK\n\n## Quick start\n\n```sh\n./pk.sh migrate\n./pk.sh start\n```\n"
    res = commands.run(tree(tmp_path / "pk", readme, PK_FILES), venv(), ("quick",))
    assert [r["status"] for r in res["quick"]] == ["ok", "serving"]


@SANDBOX
def test_an_alternative_runs_only_when_the_step_before_it_failed(tmp_path):
    files = {"ok.sh": "#!/bin/sh\necho ok\n", "bad.sh": "#!/bin/sh\necho broken >&2\nexit 3\n"}
    readme = (
        "# X\n\n## Quick start\n\n```sh\n./ok.sh one\n# or\n./bad.sh one\n"
        "./bad.sh two\n# or\n./ok.sh two\n```\n"
    )
    res = commands.run(tree(tmp_path / "x", readme, files), venv(), ("quick",))
    assert [(r["cmd"], r["status"]) for r in res["quick"]] == [
        ("./ok.sh one", "ok"),
        ("./bad.sh one", "skipped"),
        ("./bad.sh two", "failed"),
        ("./ok.sh two", "ok"),
    ]
    # the step that failed is still the README's failure
    assert commands.items(res, tests=False, restart=False)["commands"]["status"] == "red"


# LEAN3's FrontDesk: "To try it with made-up data instead, start it with ``--demo``". No start
# verb, so it ran while ``python3 frontdesk.py`` still served, and failed on its own "Port 8765
# is already in use" (a false hold: the tester got 13/13 from the same README)
FD = r"""import http.server, sys
try:
    srv = http.server.HTTPServer(("127.0.0.1", 8765), http.server.SimpleHTTPRequestHandler)
except OSError:
    print("Port 8765 is already in use. Set FRONTDESK_PORT to use another port.")
    sys.exit(1)
print("demo data loaded" if "--demo" in sys.argv else "FrontDesk is running", flush=True)
srv.serve_forever()
"""


@SANDBOX
def test_a_second_way_to_start_it_that_finds_the_port_taken_is_not_a_failure(tmp_path):
    readme = (
        "# FD\n\n## Quick start\n\n```\npython3 fd.py\n```\n\nTo try it with made-up "
        "data instead, start it with `--demo`:\n\n```\npython3 fd.py --demo\n```\n"
    )
    res = commands.run(
        tree(tmp_path / "fd", readme, {"fd.py": FD}), venv(), ("quick",), timeouts={"step_s": 6}
    )
    st = {r["cmd"]: r for r in res["quick"]}
    assert st["python3 fd.py"]["status"] == "serving"
    assert st["python3 fd.py --demo"]["status"] == "skipped"
    assert st["python3 fd.py --demo"]["alternative"]
    assert "still serves" in st["python3 fd.py --demo"]["why"]
    assert commands.items(res, tests=False, restart=False)["commands"]["status"] == "green"
    # with nothing serving before it, the same failure is the README's
    readme = "# FD\n\n## Quick start\n\n```\npython3 fd.py --port-taken\n```\n"
    files = {"fd.py": "print('Port 8765 is already in use.')\nraise SystemExit(1)\n"}
    res = commands.run(tree(tmp_path / "fd2", readme, files), venv(), ("quick",))
    assert commands.items(res, tests=False, restart=False)["commands"]["status"] == "red"


NR = r"""import sys
cmd, *args = sys.argv[1:] or ["list"]
if cmd == "import":
    try:
        open(args[0]).close()
    except OSError as e:
        print(f"nextread: cannot read {args[0]}: {e.strerror}", file=sys.stderr)
        sys.exit(2)
if cmd == "export":
    open(args[-1], "w").write("Dune\n")
if cmd == "broken":
    raise SystemExit("nextread: list file is damaged")
print(cmd, "ok")
"""


def nextread_readme(swap: dict | None = None) -> str:
    text = (FIX / "nextread-r1-README.md").read_text().replace("nextread ", "./nr.sh ")
    for a, b in (swap or {}).items():
        text = text.replace(a, b)
    return text


NR_FILES = {"nr.py": NR, "nr.sh": '#!/bin/sh\nexec python nr.py "$@"\n'}


@SANDBOX
def test_nextread_r1_s_example_import_of_the_user_s_own_list_is_not_a_failure(tmp_path):
    res = commands.run(tree(tmp_path / "nr", nextread_readme(), NR_FILES), venv(), ("quick",))
    st = {r["cmd"]: r for r in res["quick"]}
    assert st["./nr.sh import tabs.txt"]["status"] == "failed"
    assert st["./nr.sh import tabs.txt"]["missing"] == ["tabs.txt"]
    it = commands.items(res, tests=False, restart=False)["commands"]
    assert it["status"] == "green", it
    assert "a file of the user's own" in it["title"] + it["why"]


@SANDBOX
def test_a_file_an_earlier_step_wrote_and_other_failures_stay_red(tmp_path):
    # the file is written by the step before: a failure on it is the README's failure
    swap = {"./nr.sh import tabs.txt": "./nr.sh export -o tabs.txt\n./nr.sh broken tabs.txt"}
    res = commands.run(tree(tmp_path / "a", nextread_readme(swap), NR_FILES), venv(), ("quick",))
    st = {r["cmd"]: r for r in res["quick"]}
    assert st["./nr.sh export -o tabs.txt"]["status"] == "ok"
    assert "missing" not in st["./nr.sh broken tabs.txt"]
    assert commands.items(res, tests=False, restart=False)["commands"]["status"] == "red"
    # a failure that isn't about the user's file
    swap = {"./nr.sh import tabs.txt": "./nr.sh broken tabs.txt"}
    res = commands.run(tree(tmp_path / "b", nextread_readme(swap), NR_FILES), venv(), ("quick",))
    assert commands.items(res, tests=False, restart=False)["commands"]["status"] == "red"


def test_driftwatch_s_sample_output_is_read_not_run(tmp_path):
    # LEAN2's Driftwatch: "It prints something like:" then an untagged block of output; the
    # check ran "Driftwatch is running at ..." as a command (exit 127) and held for it
    (tmp_path / "README.md").write_text((FIX / "driftwatch-lean2-README.md").read_text())
    quick = [e["cmd"] for e in commands.extract(tmp_path)["quick"]]
    assert quick[:2] == ["./driftwatch.sh", "./driftwatch.sh --demo"], quick
    assert not any(c.startswith(("Driftwatch is", "First run", "http")) for c in quick)
    # a tagged block after the same words is still commands
    (tmp_path / "README.md").write_text(
        "# X\n\n## Quick start\n\nThis prints the version:\n\n```sh\n./x.sh --version\n```\n\n"
        "It prints something like:\n\n```\n./x.sh --version\n```\n"
    )
    assert [e["cmd"] for e in commands.extract(tmp_path)["quick"]] == ["./x.sh --version"]
