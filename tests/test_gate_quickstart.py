"""The gate holds on the harness's quick start (``commands``: the README's commands, run as
written on a fresh copy), not on the checks session's ``install``: Sidedoor r10 went "ready"
with ``./install.sh`` exiting 1 in the harness while the session said "install green" (AU1
#6). The session's ``install``, ``empty_start`` and ``first_admin`` are advisory rows;
``bind_localhost`` still holds, and is never overridden."""

import json
import shutil
from pathlib import Path

from repo_factory.loop import gate

FIX = Path(__file__).parent / "fixtures" / "quickstart"
DOCS = {"SECURITY.md", "READINESS.md"}


def round_with(tmp_path, change=None) -> Path:
    rd = tmp_path / "r10"
    shutil.copytree(FIX / "sidedoor-r10", rd)
    if change:
        r = json.loads((rd / "readiness.json").read_text())
        for i in r["final"]["items"]:
            i.update(change.get(i["id"], {}))
        (rd / "readiness.json").write_text(json.dumps(r))
    return rd


def test_sidedoor_r10_is_held_on_its_failing_quick_start(tmp_path):
    c = gate.record_checks(round_with(tmp_path), DOCS)
    sd = c["safe_defaults"]
    assert not sd["ok"] and sd["red"] == ["commands"]
    assert "`./install.sh` (README.md:22) exits 1" in sd["detail"]
    # the rest of the round was ready: the quick start is the only hold
    assert [k for k, v in c.items() if not v["ok"]] == ["safe_defaults"]
    # an owner may publish over it (a README fix); nothing refuses it
    plan = gate.override_plan(c, [])
    assert [k for k, _ in plan["overrides"]] == ["safe_defaults"] and not plan["refused"]


def test_the_quick_start_working_as_written_passes(tmp_path):
    c = gate.record_checks(round_with(tmp_path, {"commands": {"status": "green"}}), DOCS)
    assert c["safe_defaults"]["ok"], c["safe_defaults"]
    assert (
        c["safe_defaults"]["detail"]
        == "localhost and quick start: bind_localhost green, commands green"
    )
    # not checked here (no bwrap, a tool the sandbox lacks) or nothing to run: never a hold
    for st in ("amber", "n/a"):
        c = gate.record_checks(round_with(tmp_path / st, {"commands": {"status": st}}), DOCS)
        assert c["safe_defaults"]["ok"], st


def test_the_session_s_own_items_are_advisory(tmp_path):
    change = {
        "commands": {"status": "green"},
        "install": {"status": "red"},
        "empty_start": {"status": "red"},
        "first_admin": {"status": "red"},
    }
    c = gate.record_checks(round_with(tmp_path, change), DOCS)
    assert c["safe_defaults"]["ok"], c["safe_defaults"]


def test_the_corpus_replay_judges_the_quick_start_measured_again(tmp_path):
    # ``repo-factory corpus replay --run`` runs the quick start with the installed harness and gates on
    # that, not on the record (Sidedoor r10's ./install.sh works with the wheelhouse now)
    rd = round_with(tmp_path)
    item = {"id": "commands", "title": "quick start", "status": "green", "why": "6 worked"}
    c = gate.with_item(rd, DOCS, item, tmp_path / "again")
    assert c["safe_defaults"]["ok"] and all(v["ok"] for v in c.values())
    assert (
        json.loads((rd / "readiness.json").read_text())["final"]["items"][1]["status"] == "red"
    )  # the record is untouched


def test_listening_beyond_loopback_still_holds_and_is_never_overridden(tmp_path):
    change = {
        "commands": {"status": "green"},
        "bind_localhost": {"status": "red", "why": "binds 0.0.0.0"},
    }
    c = gate.record_checks(round_with(tmp_path, change), DOCS)
    assert not c["safe_defaults"]["ok"] and c["safe_defaults"]["red"] == ["bind_localhost"]
    plan = gate.override_plan(c, [])
    assert [k for k, _ in plan["refused"]] == ["bind_localhost"]
