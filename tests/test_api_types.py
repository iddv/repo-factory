"""The API's types: ``repo_factory/ui/types.py`` says what the page's reads
answer, and three guards keep it true. (1) The generated TypeScript is current (``gen_ts.py
--check``). (2) Every typed route's real answer on the fake app, with builds, tasks and
history in it, has the shape its TypedDict says (here). (3) ``svelte-check`` fails a component
that reads a field that is gone (CI's frontend job)."""

import copy
import importlib.util
import json
import os
import threading
import urllib.request
from pathlib import Path

import pytest
import yaml

from repo_factory.events import EventLog
from repo_factory.ui import api
from repo_factory.ui import types as T
from repo_factory.ui.server import make_server
from repo_factory.ui.v5view import STAGES

from .loop_common import make_loop

ROOT = Path(__file__).resolve().parents[1]
GONE = 4194304 + 7  # above Linux's largest pid: never a live process


def _gen_ts():
    spec = importlib.util.spec_from_file_location("gen_ts", ROOT / "scripts" / "gen_ts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def task(root: Path, name: str, pid: int, **started) -> EventLog:
    d = root / name
    d.mkdir(parents=True)
    (d / "spec.md").write_text(f"# {name}\n")
    log = EventLog(d / ".den" / "events.jsonl", name, 1)
    log.emit("build_started", pid=pid, pipeline="v5", policy="v5.0", **started)
    return log


@pytest.fixture
def app(tmp_path):
    lp = make_loop(tmp_path)
    lp.add("Freelancers lose track of invoices")
    lp.add("Small clinics still schedule patients on paper")
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    root = lp.cfg.tasks_dir
    log = task(root, "running", os.getpid(), stages=list(STAGES))
    log.emit("v5_stage", stage="scope", status="started")
    log = task(root, "stopped", GONE)
    log.emit("v5_stage", stage="build", status="started")
    log.emit("v5_stage", stage="build", status="done", usd=0.2, wall_s=30)
    return api.App(root, "fake", lp.cfg.home)


def get(app, path: str, query=None):
    code, ctype, body = api.handle("GET", "/" + path.lstrip("/"), query, app=app)
    assert code == 200 and ctype.startswith("application/json"), (path, code, body[:200])
    return json.loads(body)


def test_the_generated_typescript_is_current():
    gen = _gen_ts()
    stale = [name for name, make in gen.FILES.items() if (gen.OUT / name).read_text() != make()]
    assert not stale, f"stale: {stale}; run python3 scripts/gen_ts.py"


def test_every_typed_route_answers_its_type(app):
    seen = {}
    stopped = str(app.root / "stopped")
    query = {"api/build/runs": {"dir": stopped}, "api/run": {"dir": stopped, "n": "1"}}
    for path, tp in T.RETURNS.items():
        if path.startswith("/"):
            continue  # a server's own route: below
        seen[path] = got = get(app, path, query.get(path))
        assert T.problems(got, tp) == [], path
    s = seen["api/state"]
    # the check read real rows, not empty lists
    assert len(s["builds"]) == 2 and any(r.get("pipeline") for r in s["builds"])
    assert len(s["ideas"]) == 2 and s["history"]
    assert seen["api/actions"]["actions"]
    for kind, ref in (("idea", s["ideas"][0]["id"]), ("build", s["builds"][0]["key"])):
        assert T.problems(get(app, "api/actions", {"kind": kind, "id": ref}), T.ActionList) == [], (
            kind
        )


def test_whoami_answers_its_type(app):
    srv = make_server(app.root, port=0, harness="fake", loop_home=app.loop_home)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{srv.server_address[1]}/api/whoami", timeout=30
        ) as r:
            assert T.problems(json.loads(r.read()), T.WhoAmI) == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_renamed_or_retyped_field_is_caught(app):
    """What the check exists for: a field the page reads renamed, dropped or retyped."""
    s = get(app, "api/state")
    assert T.problems(s, T.Snapshot) == []
    bad = copy.deepcopy(s)
    bad["builds"][0]["name"] = bad["builds"][0].pop("title")  # renamed
    bad["ideas"][0]["sent"] = "no"  # retyped
    del bad["status"]["paused"]  # dropped
    bad["flagged"] = 1  # added to a closed type
    got = T.problems(bad, T.Snapshot)
    assert "$.builds[0]: no 'title' (BuildRow)" in got
    assert "$.ideas[0].sent: 'no' is not a boolean" in got
    assert "$.status: no 'paused' (LoopStatus)" in got
    assert "$: 'flagged' is not in Snapshot" in got
    # an open row may carry keys the types don't name yet; a closed one may not
    assert "pipeline" not in T.BuildRow.__annotations__ and "BuildRow" in T.OPEN
    assert T.problems({**get(app, "api/header"), "extra": 1}, T.Header) == [
        "$: 'extra' is not in Header"
    ]


def test_the_checks_rules():
    assert T.problems(1, float) == [] and T.problems(True, int) != [] and T.problems(1.5, int)
    assert T.problems(None, str | None) == [] and T.problems(3, str | None)
    assert T.problems("builds", T.ChangeKind) == [] and T.problems("chat", T.ChangeKind)
    assert T.problems({"a": 1}, dict[str, int]) == [] and T.problems({"a": "1"}, dict[str, int])
    assert T.problems([1, "2"], list[int]) == ["$[1]: '2' is not an integer"]
    with pytest.raises(TypeError):
        T.problems(1, complex)


def test_every_type_generates_and_the_stream_is_typed():
    gen = _gen_ts()
    text = gen.types_ts()
    for name in (
        "Snapshot",
        "Header",
        "Info",
        "WhoAmI",
        "ActionList",
        "StreamChange",
        "StreamHost",
    ):
        assert f"export interface {name} {{" in text, name
    assert '"api/state": Snapshot;' in text and f"API_VERSION = {api.API_VERSION};" in text
    assert "  kind?: string;" in text  # NotRequired
    assert "  [key: string]: unknown;" in text  # OPEN
    acts = gen.actions_ts()
    from repo_factory import registry as R

    for k in R.ACTIONS:
        assert f'  "{k}":' in acts, k
    confirm = [k for k in R.ACTIONS if R.get(k).click == "confirm"]
    assert confirm and all(f'  "{k}",\n' in acts for k in confirm)
