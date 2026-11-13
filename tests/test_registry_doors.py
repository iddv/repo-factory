"""Characterisation of every registry action (per verb, so a change to a door shows).

For each action kind: who may run it (the confirm class and the refusal per origin), the
subject state it runs on, and what it does when it runs: the app methods and module functions
it calls, with their arguments, as a recording fake sees them. The table is in
``tests/snapshots/registry_doors.json``; a change to any action's door or call shows up as a
diff there. Regenerate on purpose only: ``REPO_FACTORY_SNAPSHOT=1 pytest tests/test_registry_doors.py``.
No model, no network, no GitHub: every call lands on the recorder."""

import json
import os
from pathlib import Path

import pytest

from repo_factory import registry as R

SNAP = Path(__file__).parent / "snapshots" / "registry_doors.json"
ORIGINS = ("ui", "cli", "agent:rc")
# reads and checks that act on nothing: they run as they are
PURE = {"check_host", "manifest", "post_url", "clean", "activation_of"}
STATES = (
    "new",
    "queued",
    "building",
    "paused",
    "held",
    "ready",
    "published",
    "kept",
    "rejected",
    "closed",
    "error",
    "skipped",
)


class Anything(dict):
    """What a recorded call returns: any key or attribute reads as another ``Anything``."""

    def __missing__(self, key):
        return Anything()

    def get(self, key, default=None):
        return self[key]

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self[name]

    def __str__(self):
        return "x"

    __repr__ = __str__


class Recorder:
    def __init__(self, calls: list, path: str):
        self._calls, self._path = calls, path

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Recorder(self._calls, f"{self._path}.{name}")

    def __call__(self, *a, **kw):
        self._calls.append(
            [self._path, [_plain(x) for x in a], {k: _plain(v) for k, v in sorted(kw.items())}]
        )
        return Anything(id="job-1", what="refresh", ok=True, out="", source={"sha": "0" * 12})


def _plain(x):
    if isinstance(x, str) and "BEGIN" in x:
        return "<a key>"  # the snapshot never holds key-shaped text
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    if isinstance(x, Path):
        return "<path>"
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return f"<{type(x).__name__}>"


def _fields(a: R.Action) -> dict:
    out = {}
    for f in a.fields:
        if not f.required:
            continue
        if f.kind == "enum":
            out[f.name] = f.choices[0]
        elif f.kind == "int":
            out[f.name] = f.min or 1
        elif f.kind == "bool":
            out[f.name] = True
        elif f.kind == "list":
            out[f.name] = [f.choices[0]] if f.choices else ["x"]
        elif f.name == "repo":
            out[f.name] = "o/r"
        elif f.name == "project":
            out[f.name] = "p00000001"
        elif f.name == "change":
            out[f.name] = "c00000001"
        elif f.name == "claim":
            out[f.name] = "k1"
        elif f.name == "host":
            out[f.name] = "127.0.0.1"
        elif f.name == "pem":
            out[f.name] = (
                "-----BEGIN " + "RSA PRIVATE KEY-----\nx\n-----END " + "RSA PRIVATE KEY-----"
            )
        else:
            out[f.name] = "a few words of " + f.name
    return out


UNDO_UNTIL = 4_000_000_000  # 2096: still within its window


def _subjects(a: R.Action):
    """Candidate subjects, the first one the action's own check accepts is used: the plain
    states first, then rows that carry what some actions look for."""
    if "loop" in a.subjects:
        yield "loop", R.Subject("loop", cfg={})
    if "new" in a.subjects:
        yield "new", R.Subject("new", cfg={})
    for st in STATES:
        idea = {
            "id": "i0001",
            "state": st,
            "title": "t",
            "task_dir": "/nowhere/task",
            "gate": {"round": 1, "out": "/nowhere/out", "branch": "repo-factory/x"},
            "publish": {},
        }
        row = {
            "key": "/nowhere/task",
            "idea": "i0001",
            "idea_state": st,
            "state": "running",
            "status": st,
        }
        yield f"idea:{st}", R.Subject("idea", idea, row, {}, "i0001")
    gate = {"checks": {"delivered": {"ok": True}, "tests": {"ok": False, "detail": "red"}}}
    claims = {"claims": [{"id": "k1", "text": "x", "status": "open"}]}
    rich = {
        "question": {"id": "q1", "text": "which?"},
        "change": {"file": "/nowhere/c.md"},
        "undo": {"of": "stop_round", "until": UNDO_UNTIL},
        "repo": "o/r",
        "gate": gate,
        "claims": claims,
        "precheck": {"state": "done", "holds": []},
    }
    for st in ("rejected", "building", "held", "ready", "published"):
        idea = {
            "id": "i0001",
            "state": st,
            "title": "t",
            "task_dir": "/nowhere/task",
            "pitch": {"product": "p"},
            "gate": {"round": 1, "out": "/nowhere/out", "branch": "repo-factory/x", **gate},
        }
        row = {
            "key": "/nowhere/task",
            "idea": "i0001",
            "idea_state": st,
            "state": "running",
            "status": st,
            **rich,
        }
        yield f"rich:{st}", R.Subject("idea", idea, row, {}, "i0001")
    hand = {
        "key": "/nowhere/hand",
        "idea": None,
        "state": "running",
        "status": "held",
        "question": {"id": "q1", "text": "which?"},
    }
    yield "hand:running", R.Subject("build", None, hand, {}, "/nowhere/hand")
    yield (
        "hand:delivered",
        R.Subject("build", None, {**hand, "state": "delivered"}, {}, "/nowhere/hand"),
    )


def _characterise(kind: str, monkeypatch) -> dict:
    a = R.get(kind)
    calls: list = []
    app = Recorder(calls, "app")
    for mod in ("github_app", "github_user"):
        m = __import__(f"repo_factory.{mod}", fromlist=["x"])
        for name in dir(m):
            fn = getattr(m, name)
            if (
                callable(fn)
                and not isinstance(fn, type)
                and not name.startswith("_")
                and name not in PURE
            ):
                monkeypatch.setattr(m, name, Recorder(calls, f"{mod}.{name}"))
    monkeypatch.setattr("repo_factory.github_app.available", lambda: None)
    row: dict = {
        "click": a.click,
        "subjects": sorted(a.subjects),
        "refused_from": [o for o in ORIGINS if R.door_refuses(o, kind)],
    }
    used = None
    for label, s in _subjects(a):
        if R.check(kind, s) is None:
            used = (label, s)
            break
    if used is None:
        row["runs_on"] = None
        return row
    row["runs_on"] = used[0]
    ctx = R.Ctx(app=app, origin="ui", confirm=True)
    try:
        out = R.run(ctx, kind, _fields(a), used[1], record=False)
        row["result_keys"] = sorted(out)
    except Exception as e:  # noqa: BLE001 - the refusal is the behaviour
        row["raises"] = type(e).__name__
    row["calls"] = calls
    return row


def test_every_action_goes_through_the_door_it_went_through(monkeypatch):
    table = {}
    for kind in sorted(R.ACTIONS):
        with monkeypatch.context() as mp:
            table[kind] = _characterise(kind, mp)
    text = json.dumps(table, indent=1, sort_keys=True) + "\n"
    if os.environ.get("REPO_FACTORY_SNAPSHOT") == "1" or not SNAP.exists():
        SNAP.parent.mkdir(exist_ok=True)
        SNAP.write_text(text)
        pytest.skip("snapshot written")
    want = json.loads(SNAP.read_text())
    assert sorted(table) == sorted(want), "an action was added or removed: regenerate on purpose"
    for kind in sorted(table):
        assert json.loads(json.dumps(table[kind])) == want[kind], kind
