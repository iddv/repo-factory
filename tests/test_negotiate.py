"""What a claim means (NG-PLAN.md): read at build start, never negotiated at publish.

Beliefline's case (2026-10-11) replayed from fixtures: the pitch's one-liner said it "records
every change an AI agent makes to its memory … so you can rebuild exactly what the agent
believed"; the product records only what agents report to it. Rounds of rewording at publish
never converged. Since the owner's decision (2026-10-16) the public text is written from the
product, the review's replacements are applied, and wording never holds: the release text is
the product's, or the owner's as written. Claims are read only at build start, where they shape
what is built. Fakes only: no model, no network (``gh`` is ``fixtures/loop/fake_gh.py``)."""

import json
import subprocess
from pathlib import Path

import pytest

from repo_factory.errors import LoopError
from repo_factory.loop import builds
from repo_factory.loop import claims as cl

from .loop_common import FAKE_BUILD, FakeModel, TextModel, make_loop, pitch_json, wait

OVER = (
    "A Python library and small self-hostable service that records every change an AI "
    "agent makes to its memory as an append-only event, so you can rebuild exactly what "
    "the agent believed at any past moment and trace each belief back to the events "
    "behind it."
)
QUOTE = (
    "records every change an AI agent makes to its memory as an append-only event, so you "
    "can rebuild exactly what the agent believed at any past moment"
)
HONEST_TEXT = (
    "A Python library and small self-hostable service that records each memory "
    "change an AI agent reports to it as an append-only event, so you can rebuild "
    "what the agent believed at any past moment, as far as it was reported."
)
README = (
    "# Beliefline\n\nAn append-only memory log you run next to your own AI agents. Agents "
    "report their memory changes to it over a local HTTP JSON API (or the CLI). It "
    "records only what is reported to it and cannot see changes made elsewhere, so its "
    "history is only as complete as that reporting.\n\n## Install\n\n./run.sh\n"
)
FROM_PRODUCT = {
    "description": "Beliefline is a Python library and a small local service that "
    "stores the memory changes your agents report to it as "
    "append-only events, so you can read back what was reported "
    "at any past moment.",
    "does": "Stores reported agent memory changes as append-only events.",
    "release_text": "A Python library and local service that stores the memory "
    "changes your agents report to it as append-only events.",
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


def overclaim(prompt):
    """The review flags the pitch's overclaim wherever it still stands in the text it reads."""
    return (
        [
            {
                "file": "RELEASE",
                "quote": QUOTE,
                "problem": "the product only records changes agents report to it",
                "replacement": "records each memory change an agent reports to it",
            }
        ]
        if QUOTE in prompt
        else []
    )


def beliefline(tmp_path, judge, fake=None, **over):
    """Beliefline as it stood on 2026-10-11: pitched with the overclaiming one-liner, built
    (the fake build) with a README already qualified by two product rounds, gate ready."""
    lp = make_loop(
        tmp_path,
        model=FakeModel(pitch_json(product="Beliefline", product_type="library", one_liner=OVER)),
        honesty=judge,
        publish={"enabled": True, **over.pop("publish", {})},
        **over,
    )
    idea, _ = lp.add("people running their own AI agents keep getting burned by silent memory loss")
    task = Path(lp.pitch([idea["id"]])[0]["task_dir"])
    (task / "fake.json").write_text(json.dumps({"files": {"README.md": README}, **(fake or {})}))
    r = subprocess.run(["python3", str(FAKE_BUILD), str(task)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lp.gate(idea["id"])
    return lp, idea["id"], task


def repos(fake_gh) -> dict:
    p = fake_gh / "repos.json"
    return json.loads(p.read_text()) if p.exists() else {}


# -- Beliefline, replayed --------------------------------------------------------------------
def test_beliefline_publishes_with_its_text_written_from_the_product(tmp_path, fake_gh):
    judge = TextModel(FROM_PRODUCT, overclaim)
    lp, iid, task = beliefline(tmp_path, judge)
    out = lp.publish(iid)  # the command line: the checks run here, as the tick
    assert out["state"] == "published", out.get("reason")
    # one writer call, one review; no rewording round, no product round, no claim recorded
    assert judge.calls == ["describe", "review"]
    assert "change" not in out["build"] and not out["build"].get("changes")
    assert out["pitch"].get("release") is None and cl.of(out) == []
    # the writer read the product, never the pitch
    assert OVER not in judge.of("describe")[0] and "rebuild exactly" not in judge.of("describe")[0]
    r = repos(fake_gh)["owner/beliefline"]
    assert r["description"] == FROM_PRODUCT["release_text"] and QUOTE not in r["description"]
    assert r["releases"][0]["notes"].startswith(
        f"First release of Beliefline: {FROM_PRODUCT['release_text']}"
    )
    readme = subprocess.run(
        ["git", "show", "main:README.md"], cwd=r["bare"], capture_output=True, text=True
    ).stdout
    assert readme.startswith("# Beliefline\n\n" + FROM_PRODUCT["description"] + "\n\n## Install")


def test_a_build_held_on_factory_text_is_ready_again_and_publishes(tmp_path, fake_gh):
    """The live state before 2026-10-16: held "not pushed" on wording, with a done record of the
    old checks. The new checks' key differs, so they run again on the loop's next pass; nothing
    holds; the build is ready, and the loop publishes it privately. No round runs."""
    judge = TextModel(FROM_PRODUCT, overclaim)
    lp, iid, task = beliefline(tmp_path, judge)
    lp.ledger.update(
        iid,
        state="held",
        reason=f"not pushed: honesty check: '{QUOTE}': "
        "overstates capture; next: open Claims on the build page",
        publish={
            "precheck": {
                "key": "from-before",
                "state": "done",
                "blocked": [f"honesty check: '{QUOTE}': x"],
            },
            "honesty_repairs": 1,
            "negotiation": {"stuck": {"why": "again", "words": "x"}},
        },
    )
    lp.tick()  # the checks run (inline here), then: ready
    i = lp.ledger.get(iid)
    assert i["state"] == "ready" and i["reason"] is None
    assert lp.ledger.history(iid)[0]["note"].startswith("the pre-push checks pass now")
    lp.tick()
    assert lp.ledger.get(iid)["state"] == "ready"  # waits for the owner's press
    assert lp.publish(iid)["state"] == "published"
    assert not (task / ".den" / "changes").exists()  # no product round, ever


def test_claims_are_read_at_build_start_only(tmp_path, fake_gh):
    """Accept and Edit belong to an idea whose build has not started, or one held before
    building; at publish they are refused, and the registry hides them."""
    from repo_factory import registry as R

    lp, iid, task = beliefline(tmp_path, TextModel(FROM_PRODUCT))
    p = lp.ledger.get(iid)["pitch"]
    lp.ledger.update(
        iid,
        pitch={
            **p,
            "claims": [
                {
                    "id": "k1",
                    "claim": "records every change",
                    "interpreted": "records reported changes",
                    "status": "standing",
                }
            ],
        },
    )
    with pytest.raises(LoopError, match="claims are read when the build starts"):
        lp.claim_accept(iid, "k1", origin="ui")
    s = R.Subject("idea", lp.ledger.get(iid), None, lp.cfg.data, iid)
    assert R.check("accept_claim", s) and R.check("edit_claim", s)
    assert R.check("edit_release_text", s) is None  # the release text stays yours


# -- design time: the pitch's interpretations ---------------------------------------------------
def test_permanently_becomes_a_ttl_setting_with_a_90_day_default(tmp_path):
    reply = pitch_json(
        product="Keepnote",
        one_liner="Keeps your notes for a configurable "
        "time (90 days by default) and finds them again.",
        claims=[
            {
                "claim": "keeps every note permanently",
                "interpreted": "keeps notes for a configurable time, 90 days by default",
                "setting": {
                    "key": "retention.days",
                    "default": "90",
                    "means": "days a note is kept before it expires",
                },
                "keeps_spirit": True,
                "why": "notes stay findable as long as anyone needs them; "
                "forever is not a promise software can keep",
            }
        ],
    )
    lp = make_loop(tmp_path, model=FakeModel(reply))
    idea, _ = lp.add("I want a notebook that keeps every note permanently")
    out = lp.pitch([idea["id"]])[0]
    assert out["state"] == "queued"
    [c] = cl.of(out)
    assert c["setting"] == {
        "key": "retention.days",
        "default": "90",
        "means": "days a note is kept before it expires",
    }
    assert c["status"] == "standing" and c["spirit"]["keeps"]
    spec = (Path(out["task_dir"]) / "spec.md").read_text()
    assert "## How the claims are read" in spec
    assert "operator setting `retention.days` with the default 90" in spec
    assert "1 claim(s) read as recorded under Claims" in lp.ledger.history(idea["id"])[0]["note"]


def test_an_idea_whose_only_reading_loses_the_spirit_holds_with_the_proposal(tmp_path):
    reply = pitch_json(
        product="Anytongue",
        one_liner="Translates speech in real time.",
        claims=[
            {
                "claim": "translates any language perfectly",
                "interpreted": "translates between English and Spanish, with errors",
                "setting": None,
                "keeps_spirit": False,
                "why": "the idea is universal translation; two languages with "
                "errors is a different product",
            }
        ],
    )
    lp = make_loop(tmp_path, model=FakeModel(reply))
    idea, _ = lp.add("an app that translates any language perfectly")
    out = lp.pitch([idea["id"]])[0]
    assert out["state"] == "held"
    assert out["reason"].startswith(
        "can't be built as stated: \"translates any language "
        'perfectly" (proposed: "translates between English and '
        'Spanish, with errors"'
    )
    assert "Accept or edit the proposal under Claims" in out["reason"]
    assert out["gate"]["before_build"] and not lp.start_builds()  # nothing builds
    assert "How the claims are read" not in (Path(out["task_dir"]) / "spec.md").read_text()
    # Edit records the owner's reading; Accept builds it
    k = cl.of(out)[0]["id"]
    out = lp.claim_edit(idea["id"], k, "translates between English and Spanish", origin="ui")
    assert out["state"] == "held"
    out = lp.claim_accept(idea["id"], k, origin="ui")
    assert out["state"] == "queued" and not out["gate"]
    spec = (Path(out["task_dir"]) / "spec.md").read_text()
    assert "means: translates between English and Spanish." in spec


# -- never a third identical round ---------------------------------------------------------------
def test_ask_for_changes_refuses_a_third_identical_round(tmp_path):
    red = {
        "readiness": {
            "final": {
                "items": [
                    {
                        "id": "tests_green",
                        "status": "red",
                        "why": "Ran 123 tests, FAILED (errors=89)",
                    }
                ]
            }
        },
        "revise": {
            "readiness": {
                "final": {
                    "items": [
                        {
                            "id": "tests_green",
                            "status": "red",
                            "why": "Ran 124 tests, FAILED (errors=89)",
                        }
                    ]
                }
            }
        },
    }
    lp = make_loop(tmp_path)
    idea, _ = lp.add("small clinics still schedule patients on paper")
    iid = idea["id"]
    task = Path(lp.pitch([iid])[0]["task_dir"])
    (task / "fake.json").write_text(json.dumps(red))
    subprocess.run(["python3", str(FAKE_BUILD), str(task)], check=True, capture_output=True)
    assert lp.gate(iid)["state"] == "held"
    ask = "Fix the command or the test, please: the documented test command fails"
    for n in range(2):
        lp.revise(iid, ask, mode="code", origin="ui")
        lp.start_builds()
        wait(lambda: not builds.alive(lp.ledger.get(iid)["build"]["pid"]))
        lp.watch()
        assert lp.ledger.get(iid)["state"] == "held"
    with pytest.raises(
        LoopError,
        match="a third with the same instructions would change "
        "nothing. Next: say what is different",
    ):
        lp.revise(iid, ask, mode="code", origin="ui")
    hold = lp.ledger.get(iid)["reason"]
    with pytest.raises(LoopError, match="would change nothing"):
        lp.revise(iid, "- " + hold, mode="code", origin="agent:rc")  # the hold's own words
    # new instructions are a material change: they run
    out = lp.revise(
        iid,
        "The unittest discovery picks up tests/acceptance: make the README's "
        "command run only tests/test_*.py",
        mode="code",
        origin="ui",
    )
    assert out["state"] == "queued"


# -- a published build's text ---------------------------------------------------------------------
def test_the_owners_release_text_reaches_a_published_repo_as_written(tmp_path, fake_gh):
    """The owner's text is used as it is: a published repo's description and the release notes'
    first paragraph are updated with no model call; code, files and visibility stay."""
    judge = TextModel(FROM_PRODUCT)
    lp, iid, task = beliefline(tmp_path, judge)
    assert lp.publish(iid)["state"] == "published"
    judge.calls.clear()
    mine = "My own words: an append-only log of what my agents report about their memory."
    lp.set_release_text(iid, mine)  # the command line runs it here
    i = lp.ledger.get(iid)
    assert judge.calls == [] and i["publish"]["textcheck"]["state"] == "done"
    r = repos(fake_gh)["owner/beliefline"]
    assert r["description"] == mine and r["visibility"] == "private"
    assert r["releases"][0]["notes"].startswith(f"First release of Beliefline: {mine}\n\n")
    assert i["publish"]["release_text"] == mine
    calls = [json.loads(x) for x in (fake_gh / "calls.jsonl").read_text().splitlines()]
    assert not any(c[:2] == ["repo", "edit"] and "--visibility" in c for c in calls)


# -- the record ----------------------------------------------------------------------------------
def test_who_wrote_it():
    assert cl.author("RELEASE") == "repo-factory" and cl.author("NOTICE") == "repo-factory"
    assert cl.author("README.md") == "product" and cl.author("AGENTS.md") == "product"
    assert cl.author("AGENTS.md", [".github/repo-factory.yml", "AGENTS.md"]) == "repo-factory"


def test_a_reply_that_is_not_a_reading_is_dropped_and_ids_stay_stable():
    assert cl.clean({"claim": "x", "interpreted": "x"}) is None
    assert cl.clean({"claim": "every", "interpreted": "every"}) is None
    a = cl.clean(
        {
            "claim": "keeps it forever",
            "interpreted": "keeps it 90 days",
            "setting": {"key": "Retention", "default": "90"},
        }
    )
    assert a["setting"] is None  # not an area.name key
    cs = cl.merge([], [a])
    owner = {**cs[0], "status": "accepted", "interpreted": "keeps it a year"}
    again = cl.merge(
        [owner], [cl.clean({"claim": "keeps it forever", "interpreted": "keeps it a week"})]
    )
    assert again[0]["interpreted"] == "keeps it a year" and again[0]["id"] == "k1"
