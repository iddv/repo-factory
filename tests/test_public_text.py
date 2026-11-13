"""A product's public text is written from the product, and wording is never a hold (the
owner's decision, 2026-10-16).

The pre-push checks write the README's description, AGENTS.md's "what this is" and the release
text from the built product (its files, code, help, tests and checks), never from the pitch;
one review's replacements are applied and listed on the build page and in the release notes.
Only objective checks hold a push: a secret, what was taken from a studied program, a study
left unrecorded, and the gate. The release text the owner set is used as written.

Sidedoor (idea ``i235df527``) is replayed from fixtures copied read-only from the live
``loop.db`` and its delivered tree: held ten rounds by wording, it publishes on the next pass,
with the changes listed. Fakes only: no model, no network (``gh`` is ``fake_gh.py``)."""

import json
import random
import string
import subprocess
from pathlib import Path

import pytest

from repo_factory.loop import describe, gate, precheck, publish
from repo_factory.ui import api

from .loop_common import FAKE_BUILD, FakeModel, TextModel, make_loop, pitch_json

FIX = Path(__file__).parent / "fixtures" / "sidedoor"


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


def repos(fake_gh) -> dict:
    p = fake_gh / "repos.json"
    return json.loads(p.read_text()) if p.exists() else {}


def show(bare: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"main:{path}"], cwd=bare, capture_output=True, text=True
    ).stdout


# -- every pre-push check, classified ------------------------------------------------------------
# The owner asked for every pre-push check to be classified: a fact holds, prose never does.
# (The gate's checks are facts too: they hold before the pre-push checks run; see below.)
OBJECTIVE = {"secrets scan"}
PROSE = {"honesty check", "jargon scan"}


def staged(
    tmp_path, files: dict, honesty_ok: bool = True, ran: bool = False, targets: bool = False
) -> dict:
    """A staged tree and the record ``publish.stage`` leaves, for ``publish.finish``."""
    stage = tmp_path / "stage"
    for name, text in {"README.md": "# P\n\nDoes a thing.\n", **files}.items():
        (stage / name).parent.mkdir(parents=True, exist_ok=True)
        (stage / name).write_text(text)
    tdir = tmp_path / "targets"
    if targets:
        tdir.mkdir()
    return {
        "stage": str(stage),
        "targets": str(tdir) if targets else None,
        "studied": [],
        "ran": ran,
        "round_dir": None,
        "maintenance": None,
        "honesty": {
            "ok": honesty_ok,
            "issues": []
            if honesty_ok
            else [{"file": "README.md", "quote": "Does a thing.", "problem": "vague"}],
        },
    }


CFG = {"publish": {}}


def test_every_prose_check_is_never_a_hold(tmp_path):
    prep = staged(
        tmp_path, {"AGENTS.md": "# Agents\n\nBuilt by brb, see REPORT.md\n"}, honesty_ok=False
    )
    out = publish.finish(prep, CFG)
    assert out["jargon"], "the provenance scan still finds the words (and lists them)"
    assert out["blocked"] == []
    assert not {b.split(":")[0] for b in out["blocked"]} & PROSE
    # and none of them is a hold the owner could even be asked to publish over
    assert not set(gate.MAY_OVERRIDE) & {"honesty", "jargon"}


@pytest.mark.parametrize(
    "files, kw, check",
    [
        ({"config.py": 'TOKEN = "ghp_{}"\n'}, {}, "secrets scan"),
    ],
)
def test_every_objective_check_still_holds(tmp_path, files, kw, check):
    rnd = "".join(random.Random(4).choices(string.ascii_letters + string.digits, k=36))
    files = {k: v.replace("{}", rnd) for k, v in files.items()}
    out = publish.finish(staged(tmp_path, files, **kw), CFG)
    assert [b.split(":")[0] for b in out["blocked"]] == [check]
    assert check in OBJECTIVE
    # each one names its check the way Publish anyway reads it, and the never-list holds
    k = gate.PREPUSH[check]
    plan = gate.override_plan({}, [k])
    assert plan["overrides"] == [] and plan["refused"], plan


def test_the_gate_still_holds_a_build_that_cannot_do_what_it_promised(tmp_path, fake_gh):
    """A product that can't do what the idea promised is a gate failure (tests, flows), never
    a wording hold: nothing is pushed, whatever the text says."""
    lp = make_loop(tmp_path, publish={"enabled": True})
    idea, _ = lp.add("small clinics still schedule patients on paper")
    task = Path(lp.pitch([idea["id"]])[0]["task_dir"])
    (task / "fake.json").write_text(
        json.dumps(
            {
                "findings": {
                    "completeness": {
                        "done": 2,
                        "total": 3,
                        "flows": [{"id": "F3", "title": "Reminders", "status": "partial"}],
                    }
                }
            }
        )
    )
    subprocess.run(["python3", str(FAKE_BUILD), str(task)], check=True, capture_output=True)
    out = lp.gate(idea["id"])
    assert out["state"] == "held" and "completeness" in out["reason"]
    assert not repos(fake_gh)


# -- the writer: from the product, never the pitch -------------------------------------------
ONE = "Appointment booking and reminders for small clinics."
BRIEF = (
    "small clinics still schedule patients on paper. build something a receptionist can "
    "install to book appointments and send reminders, so nobody double-books and patients "
    "show up."
)


def product(tmp_path) -> Path:
    root = tmp_path / "tree"
    (root / "clinicbook").mkdir(parents=True)
    (root / "README.md").write_text(f"# Clinicbook\n\n{ONE}\n\n## Install\n\n./run.sh\n")
    (root / "clinicbook" / "__main__.py").write_text(
        "import argparse\np = argparse.ArgumentParser(prog='clinicbook')\n"
        "p.add_argument('book')\np.parse_args()\n"
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_book.py").write_text("def test_book_a_slot():\n    pass\n")
    return root


IDEA = {
    "title": "Clinicbook",
    "pitch": {
        "product": "Clinicbook",
        "one_liner": ONE,
        "brief": BRIEF,
        "who": "receptionists at small clinics",
    },
    "gate": {"checks": {"tests": {"ok": True, "detail": "green"}}},
}


def test_the_written_text_never_carries_the_pitchs_sentences(tmp_path):
    root = product(tmp_path)
    copied = {
        "description": f"{ONE} Run `clinicbook book` to book a slot.",
        "does": "Books clinic slots from the command line.",
        "release_text": "A command-line tool that books clinic appointment slots.",
    }
    hon = TextModel(copied)
    out = describe.run(
        root,
        IDEA,
        hon,
        None,
        None,
        "Clinicbook",
        runner=lambda root, argv: "usage: clinicbook [-h] book",
    )
    # never shown the pitch: the writer reads the product
    prompt = hon.of("describe")[0]
    assert ONE not in prompt and "double-books" not in prompt and "receptionist" not in prompt
    assert "clinicbook/__main__.py" in prompt and "test_book_a_slot" in prompt
    assert "$ clinicbook --help\nusage: clinicbook [-h] book" in prompt
    # the reply copied the one-liner: asked again, then the copied sentence is dropped
    assert hon.calls == ["describe", "describe", "review"]
    assert "copies the brief" in hon.of("describe")[1]
    lead = (root / "README.md").read_text().split("\n")[2]
    assert lead == "Run `clinicbook book` to book a slot."
    banned = describe.pitch_sentences(IDEA)
    for text in (lead, out["written"]["does"], out["release_text"]):
        assert describe.verbatim(text, banned) == []
    assert out["was"]["readme"] == ONE


def test_the_owners_release_text_is_used_as_written(tmp_path):
    root = product(tmp_path)
    mine = "My clinic's booking tool: it always works, every time."  # absolutes and all
    idea = {**IDEA, "pitch": {**IDEA["pitch"], "release": {"text": mine, "by": "owner"}}}
    hon = TextModel(
        {
            "description": "Clinicbook books appointment slots from the command line.",
            "does": "Books slots.",
            "release_text": "A tool that books slots.",
        },
        lambda p: [
            {"file": "RELEASE", "quote": "always works", "problem": "x", "replacement": "works"}
        ],
    )
    out = describe.run(root, idea, hon, None, None, "Clinicbook", runner=lambda *a: None)
    assert out["release_text"] == mine and out["release_by"] == "owner"
    assert "--- RELEASE" not in hon.of("review")[0]  # never reviewed, never rewritten
    assert out["reworded"] == [] and out["noted"] == []


def test_a_sentence_fixed_in_one_public_file_is_fixed_in_the_other(tmp_path):
    """Sidedoor's first publish (2026-10-17): the review fixed a sentence of the written
    description in the README, and AGENTS.md's copy of the same description kept it."""
    root = product(tmp_path)
    (root / "AGENTS.md").write_text(
        f"# AGENTS.md — Clinicbook\n\n{ONE}\n\n## What this is\n\n- **Does:** books.\n"
    )
    desc = "Clinicbook books every slot instantly. Run `clinicbook book`."
    hon = TextModel(
        {
            "description": desc,
            "does": "Books slots.",
            "release_text": "A command-line tool that books clinic slots.",
        },
        lambda p: [
            {
                "file": "README.md",
                "quote": "books every slot instantly",
                "problem": "one slot per command",
                "replacement": "books a slot",
            }
        ],
    )
    out = describe.run(root, IDEA, hon, None, None, "Clinicbook", runner=lambda *a: None)
    for name in ("README.md", "AGENTS.md"):
        text = (root / name).read_text()
        assert "Clinicbook books a slot. Run `clinicbook book`." in text
        assert "every slot instantly" not in text
    assert [r["file"] for r in out["reworded"]] == ["README.md and AGENTS.md"]


def test_factory_own_release_text_is_reviewed_and_reworded(tmp_path):
    root = product(tmp_path)
    hon = TextModel(
        {
            "description": "Clinicbook books appointment slots from the command line.",
            "does": "Books slots.",
            "release_text": "A tool that books every slot instantly.",
        },
        lambda p: [
            {
                "file": "RELEASE",
                "quote": "every slot instantly",
                "problem": "it books one slot per command",
                "replacement": "a slot per command",
            }
        ],
    )
    out = describe.run(root, IDEA, hon, None, None, "Clinicbook", runner=lambda *a: None)
    assert out["release_by"] == "product"
    assert out["release_text"] == "A tool that books a slot per command."
    assert out["reworded"][0]["file"] == "RELEASE"
    assert describe.notes_line(out["reworded"]).startswith(
        "repo-factory reworded 1 sentence before"
    )


# -- Sidedoor, replayed -------------------------------------------------------------------------
def sidedoor(tmp_path):
    """Sidedoor as it stands on live: delivered (round 10), the gate ready, held "not pushed"
    by the wording check, the owner's own release text set, the old checks' record done."""
    fx = json.loads((FIX / "idea.json").read_text())["idea"]
    model = json.loads((FIX / "model.json").read_text())
    hon = TextModel(model["describe"], lambda p: [i for i in model["review"] if i["quote"] in p])
    p = fx["pitch"]
    lp = make_loop(
        tmp_path,
        model=FakeModel(
            pitch_json(
                product="Sidedoor",
                product_type="cli",
                one_liner=p["one_liner"],
                who=p["who"],
                brief=p["brief"],
            )
        ),
        honesty=hon,
        publish={"enabled": True},
    )
    idea, _ = lp.add("does my coding agent take its secrets from the broker?")
    task = Path(lp.pitch([idea["id"]])[0]["task_dir"])
    files = {
        str(f.relative_to(FIX / "tree")): f.read_text()
        for f in (FIX / "tree").rglob("*")
        if f.is_file()
    }
    (task / "fake.json").write_text(json.dumps({"files": files}))
    subprocess.run(["python3", str(FAKE_BUILD), str(task)], check=True, capture_output=True)
    ev = task / ".den" / "v5" / "r1" / "exam" / "evidence"
    (ev / "results.json").write_text((FIX / "results.json").read_text())
    assert lp.gate(idea["id"])["state"] == "ready"
    lp.ledger.update(
        idea["id"],
        state="held",
        reason=fx["reason"],
        pitch={**lp.ledger.get(idea["id"])["pitch"], **p},
        publish=fx["publish"],
    )
    return lp, idea["id"], hon, model, fx


def test_the_fixture_is_the_owners_live_pain():
    fx = json.loads((FIX / "idea.json").read_text())
    i = fx["idea"]
    assert i["state"] == "held" and i["reason"].startswith("not pushed: honesty check")
    assert i["gate"]["verdict"] == "ready" and i["pitch"]["release"]["by"] == "owner"
    held = [h for h in fx["history"] if h["note"].startswith("held: not pushed")]
    assert len(held) >= 3  # held again and again


@pytest.mark.parametrize("press", [True, False], ids=["try-publishing-again", "loop-pass"])
def test_sidedoor_publishes_with_the_changes_listed(tmp_path, fake_gh, press):
    lp, iid, hon, model, fx = sidedoor(tmp_path)
    i = lp.ledger.get(iid)
    assert precheck.status(i, lp.cfg)["state"] == "stale"  # the old checks' key is gone
    if press:
        assert lp.retry(iid)["state"] == "ready"  # Try publishing again
        lp.tick()
    else:
        lp.tick()  # the loop's next pass
        assert lp.ledger.get(iid)["state"] == "ready"
        lp.tick()
    assert lp.ledger.get(iid)["state"] == "ready"  # waits for the owner's press
    i = lp.publish(iid)
    assert i["state"] == "published", i.get("reason")
    assert hon.calls == ["describe", "review"]  # once each, never a loop
    r = repos(fake_gh)["owner/sidedoor"]
    # the owner's release text, as written
    assert r["description"] == fx["pitch"]["release"]["text"]
    notes = r["releases"][0]["notes"]
    assert notes.startswith("First release of Sidedoor: " + fx["pitch"]["release"]["text"])
    assert f"repo-factory reworded {len(model['review'])} sentences before this release" in notes
    # the public text, written from the product; the pitch's words nowhere in it
    readme, agents = show(r["bare"], "README.md"), show(r["bare"], "AGENTS.md")
    assert readme.startswith("# Sidedoor\n\n" + model["describe"]["description"] + "\n\n")
    assert f"- **Does:** {model['describe']['does']}" in agents
    assert "snuck around it via" not in readme.split("```")[0]
    banned = describe.pitch_sentences({"pitch": fx["pitch"]})
    assert describe.verbatim(model["describe"]["description"], banned) == []
    for x in model["review"]:
        assert x["replacement"] in (agents if x["file"] == "AGENTS.md" else readme)
    # the build page lists them ("repo-factory reworded")
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    code, _, body = api.handle("GET", "/api/state", app=app)
    row = next(b for b in json.loads(body)["builds"] if b.get("idea") == iid)
    pt = row["claims"]["public"]
    assert pt["written"] and pt["release_by"] == "owner"
    assert [x["now"] for x in pt["reworded"]] == [x["replacement"] for x in model["review"]]
