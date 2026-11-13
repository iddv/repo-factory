"""The owner's prompts (custom_prompts): the registry, house rules, additions, full
overrides with versions, the default-changed flag, preview, provenance, and a measurement
using the shipped prompts. Offline: no model, no network."""

import dataclasses
import json
import re
import secrets
import shutil
from pathlib import Path

import pytest

from repo_factory import custom_prompts as cp
from repo_factory.events import fold, read_events
from repo_factory.resources import POLICIES
from repo_factory.ui import api
from repo_factory.v5 import common, pipeline, scope

from . import test_v5_pipeline as base

ROOT = Path(__file__).resolve().parents[1]
V5 = POLICIES / "v5"
BRIEF_IDS = {
    "scope",
    "claims",
    "verifier",
    "repair",
    "reverify",
    "readiness",
    "security",
    "oracle",
    "pair",
    "postmortem",
    "reproduce",
    "fix",
    "pitch",
    "honesty",
    "triage",
    "lesson_issue",
    "build",
    "change",
    "product",
    "describe",
    "charter",
    "evolve",
}


@pytest.fixture
def st(tmp_path, monkeypatch):
    """An empty store in a scratch repo-factory home; customisations on."""
    monkeypatch.setenv(cp.HOME_ENV, str(tmp_path / "home"))
    monkeypatch.delenv(cp.DEFAULTS_ENV, raising=False)
    return cp.store()


def token() -> str:
    """A secret-shaped value built at runtime (never a literal in the repo)."""
    return "sk-" + "ant-" + secrets.token_hex(16)


# -- the registry ----------------------------------------------------------------------------------
def test_the_registry_lists_every_prompt_and_every_file_exists():
    assert {e.id for e in cp.REGISTRY} == BRIEF_IDS
    for e in cp.REGISTRY:
        assert e.path.is_file(), e.file
        assert (ROOT / e.file).is_file(), e.file  # the repo path it names
        assert e.editable in ("full", "additions-only")
        assert e.group in (cp.PITCH, cp.BUILD, cp.MAINT)
        for name in e.placeholders:  # every placeholder is in the file
            assert name in cp.placeholders_in(e, e.path.read_text()), (e.id, name)
    # the checkers can't be replaced; the steps that decide what gets built take house rules
    assert {e.id for e in cp.REGISTRY if e.checker} == {
        "honesty",
        "security",
        "oracle",
        "readiness",
    }
    assert {e.id for e in cp.REGISTRY if e.house_rules} == {
        "pitch",
        "scope",
        "build",
        "readiness",
        "fix",
        "change",
    }
    for e in cp.REGISTRY:
        if e.house_rules:
            assert "<!-- slot: house_rules -->" in e.path.read_text(), e.id
    # nothing from the research side
    assert not any(
        e.file.startswith(("repo_factory/hsuite", "repo_factory/bench")) for e in cp.REGISTRY
    )


def test_the_defaults_compose_to_the_files_without_their_slot_lines(st):
    for e in cp.REGISTRY:
        raw = e.path.read_text()
        eff = cp.effective(e.id, noted=False)
        assert "<!-- slot:" not in eff.text
        assert eff.text == cp.SLOT.sub("", raw)
        assert eff.template == "default" and not eff.custom and eff.n == 0
    # the build stage: a spec that passes through gets exactly the brief and the vanilla
    # suffix, as before; a scoped build also gets what ships with the product (EX-PLAN §4),
    # just before the suffix
    md = "# Scope: X\n\n## Definition of done\n- [ ] F1: a — b\n"
    for m in (md, None):
        old = scope.spec_text("an idea", m).rstrip() + "\n\nWork in the current directory.\n"
        assert scope.build_prompt("an idea", m, pipeline.VANILLA_SUFFIX) == old
    assert (
        scope.build_prompt("an idea", None, pipeline.VANILLA_SUFFIX, V5)
        == scope.spec_text("an idea", None).rstrip() + "\n\nWork in the current directory.\n"
    )
    scoped = scope.build_prompt("an idea", md, pipeline.VANILLA_SUFFIX, V5)
    rules = (V5 / "prompts" / "product.md").read_text().strip()
    assert (
        scoped
        == scope.spec_text("an idea", md).rstrip()
        + "\n\n"
        + rules
        + "\n\nWork in the current directory.\n"
    )
    assert "AGENTS.md" in rules and "its own tests" in rules.lower()


# -- house rules and additions ---------------------------------------------------------------------
def test_house_rules_and_additions_land_in_their_slots(st):
    cp.set_house_rules("Only command-line tools. Licence prose is ignored.")
    cp.save("scope", {"additions": "Name every product in two words."})
    main = common.prompt_text(V5, "scope")
    judge = common.prompt_text(V5, "scope", "judge")
    i_idea, i_house = main.index("<<idea>>"), main.index(cp.HOUSE_HEADING)
    assert i_idea < i_house < main.index("=== What to write ===")
    assert cp.HOUSE_INTRO in main and "Only command-line tools." in main
    # additions: no slot in scope.md, so the end of the main part, never inside a section
    assert main.rstrip().endswith("## The owner's additions\n\nName every product in two words.")
    assert "two words" not in judge and cp.HOUSE_HEADING not in judge
    # the build stage: the house rules before the suffix; additions at the end
    cp.save("build", {"additions": "Commit often."})
    b = scope.build_prompt("an idea", None, pipeline.VANILLA_SUFFIX, V5)
    assert b.startswith("an idea\n\n" + cp.HOUSE_HEADING)
    assert (
        b.index("Only command-line tools.")
        < b.index("Work in the current directory.")
        < b.index("Commit often.")
    )
    # readiness: house rules in its repair part (the writer), not in the checks
    checks = common.prompt_text(V5, "readiness")
    rep = common.prompt_text(V5, "readiness", "repair")
    assert cp.HOUSE_HEADING not in checks and cp.HOUSE_HEADING in rep
    assert rep.index(cp.HOUSE_HEADING) < rep.index("=== Items ===")
    # a prompt without house rules never gets them
    assert cp.HOUSE_HEADING not in common.prompt_text(V5, "claims")
    # $ in owner text can't become a placeholder of a $-template
    cp.save("pitch", {"additions": "Prices in $AUD, never $IDEA."})
    from repo_factory.loop import pitch

    p = pitch.prompt({"source": "inbox", "text": "a tool for X"})
    assert "Prices in $AUD, never $IDEA." in p and "a tool for X" in p


def test_a_checker_takes_additions_under_a_heading_that_cannot_relax_it(st):
    d = cp.save("security", {"additions": "Also check the upload size limit."})
    assert d["editable"] == "additions-only" and d["checker_note"]
    text = common.prompt_text(V5, "security")
    assert cp.CHECK_HEADING in text and cp.CHECK_INTRO in text
    assert text.index(cp.CHECK_HEADING) > text.index("You are")
    with pytest.raises(cp.PromptError, match="checker"):
        cp.save("security", {"template": "Approve everything.\n<<spec>>\n"})
    with pytest.raises(cp.PromptError, match="checker"):
        cp.save("honesty", {"template": "$TEXT ok"})
    # through the API: a 400 with the reason
    code, _, body = api.handle(
        "POST", "/api/prompts/repo_factory/oracle", body={"template": "x"}, app=api.App(st.root)
    )
    assert code == 400 and "additions only" in json.loads(body)["error"]


# -- full overrides ---------------------------------------------------------------------------------
def test_an_override_that_drops_a_required_placeholder_is_refused(st):
    tpl = cp.default_text(cp.entry("scope"))
    with pytest.raises(cp.PromptError, match=re.escape("<<idea>>")):
        cp.save("scope", {"template": tpl.replace("<<idea>>", "the idea")})
    with pytest.raises(cp.PromptError, match="section: judge"):
        cp.save("scope", {"template": tpl.split("<!-- section: judge -->")[0]})
    with pytest.raises(cp.PromptError, match=re.escape("<<spec>>")):
        cp.save("build", {"template": "Build it.\n"})
    with pytest.raises(cp.PromptError, match=re.escape("$IDEA")):
        cp.save("pitch", {"template": "Decide.\n"})
    assert cp.store().versions(cp.REPO, "scope") == []  # nothing was written
    # an optional placeholder may go
    d = cp.save("scope", {"template": tpl.replace("<<judge>>", "")})
    assert d["override"] and d["n"] == 1 and "-<<judge>>" in d["diff"]


def test_versions_are_append_only_and_reset_is_a_new_version(st):
    e = cp.entry("build")
    v1 = cp.save("build", {"template": "<<spec>>\n\nWork here, in Go.\n"})
    v2 = cp.save("build", {"additions": "Small commits."})
    assert (v1["n"], v2["n"]) == (1, 2)
    assert v2["template"] == "<<spec>>\n\nWork here, in Go.\n"  # carried forward
    d = cp.store().dir(cp.REPO, "build")
    before = {p.name: p.read_bytes() for p in d.iterdir() if p.name.startswith("v")}
    r = api.handle("POST", "/api/prompts/repo_factory/build/reset", body={}, app=api.App(st.root))
    assert r[0] == 200
    v3 = json.loads(r[2])
    assert v3["n"] == 3 and v3["template"] is None and v3["additions_text"] == ""
    assert [v["action"] for v in v3["versions"]] == ["template", "additions", "reset"]
    after = {p.name: p.read_bytes() for p in d.iterdir() if p.name.startswith("v")}
    assert {k: after[k] for k in before} == before  # nothing rewritten
    assert set(after) - set(before) == {"v3.json"}
    assert cp.effective("build", noted=False).text == cp.SLOT.sub("", e.path.read_text())
    # the store refuses to overwrite a version file
    with pytest.raises(FileExistsError):
        cp._create(d / "v1.json", "{}")


def test_the_default_changed_flag_and_the_three_way_view(st, monkeypatch):
    e = cp.entry("claims")
    old = cp.default_text(e)
    mine = "Be careful.\n\n" + old
    d = cp.save("claims", {"template": mine})
    assert d["default_changed"] is False and "three_way" not in d
    new = old + "\nOne more rule from an upgrade.\n"
    monkeypatch.setattr(
        cp, "default_text", lambda x: new if x.id == "claims" else x.path.read_text()
    )
    d = cp.detail("claims")
    assert d["default_changed"] is True and d["notice"].startswith("the default changed")
    tw = d["three_way"]
    assert (tw["old_default"], tw["new_default"], tw["yours"]) == (old, new, mine)
    assert "+One more rule from an upgrade." in tw["diff_default"]
    assert next(p for p in cp.registry() if p["id"] == "claims")["default_changed"]
    # the owner's override still runs until they choose
    assert cp.effective("claims", noted=False).text.startswith("Be careful.")


# -- going back: restore, see a version, notes ---------------------------------------------------------
def test_a_restore_is_a_new_version_with_the_old_content_and_the_same_hash(st):
    d1 = cp.save(
        "build",
        {
            "template": "<<spec>>\n\nWork here, in Go.\n",
            "additions": "Small.",
            "note": "  the  good one\n",
        },
    )
    h1 = d1["version"]
    cp.save("build", {"template": "<<spec>>\n\nWork here, in Rust.\n"})
    cp.reset("build")
    d = cp.store().dir(cp.REPO, "build")
    before = {p.name: p.read_bytes() for p in d.iterdir() if p.name.startswith("v")}
    app = api.App(st.root)
    code, _, body = api.handle(
        "POST", "/api/prompts/repo_factory/build/restore", body={"version": 1}, app=app
    )
    assert code == 200, body
    v4 = json.loads(body)
    assert v4["n"] == 4 and v4["template"] == "<<spec>>\n\nWork here, in Go.\n"
    assert v4["additions_text"] == "Small." and v4["version"] == h1  # provenance: same hash
    rows = v4["versions"]
    assert [r["n"] for r in rows] == [1, 2, 3, 4]  # nothing deleted
    assert (rows[3]["action"], rows[3]["from"], rows[3]["same_as"]) == ("restore", 1, 1)
    assert rows[3]["active"] and not any(r["active"] for r in rows[:3])
    assert rows[0]["note"] == "the good one" and rows[3]["note"] == ""
    after = {p.name: p.read_bytes() for p in d.iterdir() if p.name.startswith("v")}
    assert {k: after[k] for k in before} == before  # nothing rewritten
    assert json.loads((d / "active.json").read_text())["version"] == 4  # forwards, never back
    assert cp.effective("build", noted=False).version == h1
    # restoring a default-only version (additions) also works, and reset's version too
    out = cp.restore("build", {"version": 3})
    assert out["n"] == 5 and out["template"] is None and not out["customised"]
    # unknown or bad versions
    assert (
        api.handle(
            "POST", "/api/prompts/repo_factory/build/restore", body={"version": 99}, app=app
        )[0]
        == 404
    )
    assert (
        api.handle(
            "POST", "/api/prompts/repo_factory/build/restore", body={"version": "x"}, app=app
        )[0]
        == 400
    )
    assert api.handle("GET", "/api/prompts/repo_factory/build/restore", app=app)[0] == 405


def test_a_version_shows_its_text_and_a_diff_against_the_one_in_use(st):
    cp.save("claims", {"additions": "List the edge cases first."})
    cp.save("claims", {"additions": "Be brief."})
    app = api.App(st.root)
    code, _, body = api.handle("GET", "/api/prompts/repo_factory/claims/versions/1", app=app)
    assert code == 200, body
    v = json.loads(body)
    assert (v["n"], v["template"], v["template_kind"]) == (1, None, "default")
    assert v["additions"] == "List the edge cases first." and v["active_n"] == 2
    assert "-Be brief." in v["diff"] and "+List the edge cases first." in v["diff"]
    assert v["restorable"] and v["why_not"] is None and not v["same_as_active"]
    assert api.handle("GET", "/api/prompts/repo_factory/claims/versions/7", app=app)[0] == 404
    cp.restore("claims", {"version": 1})
    v = cp.version_view("claims", 1)
    assert v["same_as_active"] and v["diff"] == "" and v["hash"] == v["active_hash"]


def test_restoring_a_version_that_lacks_a_placeholder_now_required_is_refused(st, monkeypatch):
    tpl = cp.default_text(cp.entry("scope")).replace("<<judge>>", "")  # optional then
    cp.save("scope", {"template": tpl})
    cp.reset("scope")
    stricter = dataclasses.replace(
        cp.BY_ID["scope"],
        placeholders={**cp.BY_ID["scope"].placeholders, "judge": ("the judge section", True)},
    )
    monkeypatch.setitem(cp.BY_ID, "scope", stricter)
    v = cp.version_view("scope", 1)
    assert not v["restorable"] and "<<judge>>" in v["why_not"]
    with pytest.raises(cp.PromptError, match=re.escape("not restored: v1")) as x:
        cp.restore("scope", {"version": 1})
    assert "must keep <<judge>>" in str(x.value)
    code, _, body = api.handle(
        "POST", "/api/prompts/repo_factory/scope/restore", body={"version": 1}, app=api.App(st.root)
    )
    assert code == 400 and "<<judge>>" in json.loads(body)["error"]
    assert [x["n"] for x in cp.store().versions(cp.REPO, "scope")] == [1, 2]  # nothing written


def test_a_checker_cant_be_restored_to_a_template(st, monkeypatch):
    cp.save("claims", {"template": "Be careful.\n\n" + cp.default_text(cp.entry("claims"))})
    cp.save("claims", {"additions": "Also this."})
    monkeypatch.setitem(
        cp.BY_ID, "claims", dataclasses.replace(cp.BY_ID["claims"], editable="additions-only")
    )
    with pytest.raises(cp.PromptError, match="checker"):
        cp.restore("claims", {"version": 1})
    assert not cp.version_view("claims", 1)["restorable"]
    # a checker's additions-only version restores fine
    cp.save("security", {"additions": "Check uploads."})
    cp.save("security", {"additions": ""})
    assert cp.restore("security", {"version": 1})["additions_text"] == "Check uploads."


def test_notes_round_trip_and_stay_beside_the_version(st):
    cp.save("pitch", {"additions": "Prefer tools for volunteers."})
    d = cp.store().dir(cp.REPO, "pitch")
    v1 = (d / "v1.json").read_bytes()
    app = api.App(st.root)
    code, _, body = api.handle(
        "POST",
        "/api/prompts/repo_factory/pitch/versions/1/note",
        body={"note": "best pitch so far"},
        app=app,
    )
    assert code == 200 and json.loads(body)["versions"][0]["note"] == "best pitch so far"
    assert cp.version_view("pitch", 1)["note"] == "best pitch so far"
    assert (d / "v1.json").read_bytes() == v1  # the version untouched
    cp.set_note("pitch", 1, {"note": "renamed"})
    assert cp.detail("pitch")["versions"][0]["note"] == "renamed"
    cp.set_note("pitch", 1, {"note": ""})
    assert cp.detail("pitch")["versions"][0]["note"] == ""
    assert len((d / "notes.jsonl").read_text().splitlines()) == 3  # append-only too
    assert (
        api.handle(
            "POST",
            "/api/prompts/repo_factory/pitch/versions/1/note",
            body={"note": "x" * 81},
            app=app,
        )[0]
        == 400
    )
    assert (
        api.handle(
            "POST", "/api/prompts/repo_factory/pitch/versions/5/note", body={"note": "x"}, app=app
        )[0]
        == 404
    )
    assert api.handle("GET", "/api/prompts/repo_factory/pitch/versions/1/note", app=app)[0] == 405
    with pytest.raises(cp.PromptError, match="80"):
        cp.save("pitch", {"additions": "y", "note": "z" * 81})
    assert len(cp.store().versions(cp.REPO, "pitch")) == 1


def test_house_rules_restore_see_and_note(st):
    app = api.App(st.root)
    cp.set_house_rules("Prefer SQLite.", note="calm")
    cp.set_house_rules("Prefer Postgres.")
    code, _, body = api.handle("GET", "/api/prompts/house_rules/versions/1", app=app)
    v = json.loads(body)
    assert code == 200 and v["text"] == "Prefer SQLite." and v["note"] == "calm"
    assert "-Prefer Postgres." in v["diff"] and "+Prefer SQLite." in v["diff"]
    scope_before = cp.effective("scope", noted=False).version
    code, _, body = api.handle(
        "POST", "/api/prompts/house_rules/restore", body={"version": 1}, app=app
    )
    h = json.loads(body)
    assert code == 200 and h["text"] == "Prefer SQLite." and h["version"] == 3
    last = h["versions"][-1]
    assert (last["action"], last["from"], last["same_as"], last["active"]) == (
        "restore",
        1,
        1,
        True,
    )
    assert cp.effective("scope", noted=False).version != scope_before
    api.handle("POST", "/api/prompts/house_rules/versions/2/note", body={"note": "pg"}, app=app)
    assert cp.house_rules_view()["versions"][1]["note"] == "pg"
    assert [x["n"] for x in cp.house_rules_view()["versions"]] == [1, 2, 3]


# -- preview -----------------------------------------------------------------------------------------
def test_preview_renders_a_real_build_without_a_model_and_redacts_a_secret(
    st, tmp_path, monkeypatch
):
    def no_model(*a, **k):
        raise AssertionError("a preview called a model")

    monkeypatch.setattr("repo_factory.costs.ledger.call_claude", no_model)
    root = tmp_path / "tasks"
    t = root / "shelf"
    t.mkdir(parents=True)
    (t / "task.yaml").write_text("id: shelf\nkind: repo\n")
    (t / "spec.md").write_text("# Shelf\n\nA lending desk for a small library.\n")
    r1 = t / ".den" / "v5" / "r1"
    r1.mkdir(parents=True)
    (r1 / "SCOPE.md").write_text("# Scope: Shelf\n\n## Core flows\n### F1: Lend\n")
    planted = token()
    cp.set_house_rules(f"Use the team's key api_key = '{planted}' and nothing else.")
    app = api.App(root)
    code, _, body = api.handle(
        "POST",
        "/api/prompts/preview",
        body={"repo": "repo_factory", "id": "build", "subject": "shelf"},
        app=app,
    )
    assert code == 200, body
    out = json.loads(body)
    assert out["model_called"] is False and out["subject"]["build"] == "shelf"
    assert out["text"].startswith("# Shelf\n\nA lending desk") and "# Scope: Shelf" in out["text"]
    assert out["text"].rstrip().endswith("Work in the current directory.")
    assert planted not in out["text"] and "[redacted: Anthropic key]" in out["text"]
    assert out["redactions"] and all(planted not in json.dumps(h) for h in out["redactions"])
    # a placeholder the item can't give shows as a described stand-in
    out = json.loads(
        api.handle(
            "POST",
            "/api/prompts/preview",
            body={"repo": "repo_factory", "id": "claims", "subject": "shelf"},
            app=app,
        )[2]
    )
    assert (
        "A lending desk" in out["text"] and "[lessons: the lessons the claims writer" in out["text"]
    )
    # an unknown subject is a 404, an unknown prompt too
    assert (
        api.handle(
            "POST", "/api/prompts/preview", body={"id": "build", "subject": "nope"}, app=app
        )[0]
        == 404
    )
    assert api.handle("GET", "/api/prompts/repo_factory/nope", app=app)[0] == 404


def test_preview_of_the_pitch_against_an_idea_in_the_loop(st, tmp_path, monkeypatch):
    from repo_factory.loop.ledger import Ledger

    home = tmp_path / "loop"
    monkeypatch.setenv("REPO_FACTORY_HOME", str(home))
    led = Ledger(home / "loop.db")
    idea, _ = led.add("Choirs lose track of who has which score.", "typed")
    cp.save("pitch", {"additions": "Prefer tools for volunteers."})
    app = api.App(tmp_path / "tasks", loop_home=home)
    (tmp_path / "tasks").mkdir()
    out = json.loads(
        api.handle(
            "POST", "/api/prompts/preview", body={"id": "pitch", "subject": idea["id"]}, app=app
        )[2]
    )
    assert "Choirs lose track" in out["text"] and "Prefer tools for volunteers." in out["text"]
    assert out["custom"] and out["n"] == 1


# -- the API ------------------------------------------------------------------------------------------
def test_the_api_lists_the_prompts_and_the_licence_beside_the_house_rules(
    st, tmp_path, monkeypatch
):
    home = tmp_path / "loop"
    monkeypatch.setenv("REPO_FACTORY_HOME", str(home))
    app = api.App(tmp_path, loop_home=home)
    code, _, body = api.handle("GET", "/api/prompts", app=app)
    out = json.loads(body)
    assert code == 200 and {p["id"] for p in out["prompts"]} == BRIEF_IDS
    assert set(out.get("repos") or {}) <= {"repo_factory"}
    lic = out["settings"]["publish.license"]
    assert (lic["value"], lic["source"], lic["label"]) == (
        "MIT",
        "default",
        "licence: MIT (default)",
    )
    code, _, body = api.handle(
        "POST", "/api/prompts/license", body={"license": "Apache-2.0"}, app=app
    )
    assert (
        code == 200
        and json.loads(body)["publish.license"]["label"] == "licence: Apache-2.0 (setting)"
    )
    assert api.handle("POST", "/api/prompts/license", body={"license": "GPL"}, app=app)[0] == 400
    code, _, body = api.handle(
        "POST", "/api/prompts/house_rules", body={"text": "Prefer SQLite."}, app=app
    )
    h = json.loads(body)
    assert code == 200 and h["text"] == "Prefer SQLite." and h["version"] == 1
    assert h["settings"]["publish.license"]["value"] == "Apache-2.0"
    assert (
        json.loads(api.handle("GET", "/api/prompts/house_rules", app=app)[2])["text"]
        == "Prefer SQLite."
    )
    d = json.loads(api.handle("GET", "/api/prompts/repo_factory/scope", app=app)[2])
    assert d["editable"] == "full" and d["house_rules"] and d["subject"] == "idea"
    assert {p["name"] for p in d["placeholders"]} == {"idea", "judge"}
    assert "Prefer SQLite." in d["active"] and "<<idea>>" in d["default"]
    assert api.handle("POST", "/api/prompts/repo_factory/scope", body={"x": 1}, app=app)[0] == 400


# -- defaults only: a measurement -------------------------------------------------------------------
def test_a_measurement_ignores_the_owners_prompts(st, tmp_path, monkeypatch):
    monkeypatch.setenv(cp.DEFAULTS_ENV, "")  # undone after the test
    cp.set_house_rules("Only Rust.")
    cp.save("claims", {"additions": "List twice as many."})
    assert "Only Rust." in common.prompt_text(V5, "scope")
    from repo_factory.cli import MEASURING

    assert MEASURING == ("lessons",)  # what `repo-factory lessons` sets
    cp.use_defaults()
    assert cp.defaults_only()
    for e in cp.REGISTRY:
        eff = cp.effective(e.id, noted=False)
        assert eff.text == cp.SLOT.sub("", e.path.read_text()) and not eff.custom, e.id
    assert "Only Rust." not in common.prompt_text(V5, "scope")
    assert cp.build_versions(V5)["claims"] == cp.sha(
        cp.SLOT.sub("", cp.entry("claims").path.read_text())
    )


# -- provenance -------------------------------------------------------------------------------------
class Recording(base.Scripted):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def run(self, prompt, workspace, timeout_s, **opts):
        self.prompts.append(prompt)
        return super().run(prompt, workspace, timeout_s, **opts)


def test_a_fake_build_records_each_prompts_id_and_version(st, tmp_path):
    t = tmp_path / "task"
    shutil.copytree(base.FIXTURE, t)
    shutil.rmtree(t / "_fake_solution")
    cp.set_house_rules("Keep the dependencies to the standard library.")
    cp.save("claims", {"additions": "Also claim the error messages."})
    want = {i: cp.effective(i, noted=False).version for i in ("build", "claims", "verifier")}
    h = Recording()
    sol = tmp_path / "solution" / "textstats"
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(base.BUGGY)
    h.fake.solution = sol.parent
    llm = base.llm()
    out = pipeline.build(
        t, den=tmp_path / "den", out=tmp_path / "out", harness=h, llm=llm, policy=V5
    )
    assert out["finished"]
    # what the model calls received
    build_prompt = next(p for p in h.prompts if p.rstrip().endswith("current directory."))
    assert "Keep the dependencies to the standard library." in build_prompt
    assert any("Also claim the error messages." in p for p in llm.prompts if "task=claims" in p)
    evs = read_events(Path(out["events"]))
    started = next(e for e in evs if e["type"] == "build_started")
    assert started["prompts"]["claims"] == want["claims"] and "security" in started["prompts"]
    pe = {e["id"]: e for e in evs if e["type"] == "prompt"}
    assert pe["claims"]["version"] == want["claims"] and pe["claims"]["custom"] is True
    assert pe["claims"]["n"] == 1 and pe["claims"]["additions"] is True
    assert pe["claims"]["stage"] == "claims"
    assert pe["build"]["version"] == want["build"] and pe["build"]["house_rules"] is True
    assert pe["verifier"]["version"] == want["verifier"] and pe["verifier"]["custom"] is False
    # one event per prompt and version, however often it is rendered
    assert sum(1 for e in evs if e["type"] == "prompt" and e["id"] == "claims") == 1
    done = {e["stage"]: e for e in evs if e["type"] == "v5_stage" and e.get("status") == "done"}
    assert done["claims"]["prompts"] == {"claims": want["claims"]}
    assert done["build"]["prompts"] == {"build": want["build"]}
    v = fold(evs)["v5"]
    assert v["stages"]["claims"]["prompts"]["claims"] == want["claims"]
    assert v["prompts"]["claims"]["custom"] is True


def test_each_builds_ledger_gets_its_own_prompt_events(st, tmp_path):
    # a ledger freed by an earlier build hands its id() to the next one; the next build still
    # records the prompts it serves
    class Log:
        def __init__(self):
            self.got = []

        def emit(self, kind, **kw):
            self.got.append((kind, kw["id"]))

    from repo_factory.costs import ledger

    eff = cp.effective("verifier", noted=False)
    for _ in range(20):
        log = Log()
        with ledger.activate(tmp_path / "ledger.jsonl", events=log):
            cp.note(eff)
            cp.note(eff)
        assert log.got == [("prompt", "verifier")]


def test_the_pitch_records_its_prompt_version(st):
    from repo_factory.loop import pitch

    cp.save("pitch", {"additions": "Prefer offline tools."})
    reply = json.dumps(
        {
            "verdict": "reject",
            "product": "X",
            "product_type": "cli",
            "who": "someone",
            "buildability": 0.2,
            "buildability_reasons": ["too vague"],
            "reject_reason": "vague",
        }
    )
    res = pitch.run({"source": "inbox", "text": "an idea"}, lambda body: (reply, 0.01))
    assert res["prompt"] == {
        "id": "pitch",
        "version": cp.effective("pitch", noted=False).version,
        "n": 1,
        "custom": True,
    }
