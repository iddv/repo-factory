"""The pitch: its contract, rejection with reasons, re-pitching, and the task it makes."""

import json
from pathlib import Path

import yaml

from repo_factory.loop import pitch
from repo_factory.tasks import load_task

from .loop_common import FakeModel, make_loop, pitch_json


def test_contract_accepts_a_good_reply():
    p = pitch.normalise(json.loads(pitch_json()))
    assert pitch.check(p) == []
    assert pitch.sentences(p["brief"]) == 2


def test_contract_problems():
    bad = pitch.normalise(
        json.loads(
            pitch_json(product_type="mobile app", buildability="x", brief="One sentence only")
        )
    )
    probs = pitch.check(bad)
    assert any("product_type" in x for x in probs)
    assert any("buildability" in x for x in probs)
    assert any("2-4 sentences" in x for x in probs)
    long = pitch.normalise(json.loads(pitch_json(brief="A. B. C. D. E.")))
    assert any("2-4 sentences" in x for x in pitch.check(long))
    bullets = pitch.normalise(json.loads(pitch_json(brief="- one\n- two\n- three")))
    assert any("bullets" in x for x in pitch.check(bullets))
    rej = pitch.normalise(json.loads(pitch_json(verdict="reject", reject_reason="")))
    assert any("reject_reason" in x for x in pitch.check(rej))


def test_malformed_reply_is_asked_again_once():
    m = FakeModel(["sorry, no json", "```json\n" + pitch_json() + "\n```"])
    res = pitch.run({"source": "cli", "text": "an idea"}, m)
    assert res["decision"] == "accepted" and len(m.prompts) == 2
    assert "broke the contract" in m.prompts[1]
    m = FakeModel("still no json")
    res = pitch.run({"source": "cli", "text": "an idea"}, m)
    assert res["decision"] == "malformed" and len(m.prompts) == 2


def test_low_buildability_is_a_rejection_with_the_reason():
    res = pitch.run(
        {"source": "cli", "text": "x"},
        FakeModel(pitch_json(buildability=0.4)),
        min_buildability=0.6,
    )
    assert res["decision"] == "rejected"
    assert "buildability 0.40 is under" in res["pitch"]["reject_reason"]


def test_accepted_idea_becomes_a_task(tmp_path):
    lp = make_loop(tmp_path)
    idea, _ = lp.add("small clinics still schedule patients on paper")
    [out] = lp.pitch()
    assert out["state"] == "queued" and out["title"] == "Clinicbook"
    task = tmp_path / "tasks" / "clinicbook"
    assert out["task_dir"] == str(task)
    spec = (task / "spec.md").read_text()
    # the Shape line from product_type and who (receptionists at small clinics: a business)
    assert spec.startswith(
        "# Clinicbook\n\nShape: business, web app, no money\n\n"
        "small clinics still schedule patients on paper."
    )
    assert "Grounding:" not in spec  # the live pitch returns no grounding lines
    cfg = yaml.safe_load((task / "task.yaml").read_text())
    assert cfg["budget_usd"] is None and cfg["id"] == "clinicbook"
    t, _ = load_task(task)
    assert "receptionist" in t.spec
    meta = json.loads((task / "loop.json").read_text())
    assert meta["idea"] == idea["id"] and meta["pitch"]["product_type"] == "web app"
    # the spend of the call is recorded
    assert lp.spend()["today"]["loop"] > 0
    # a second accepted idea with the same product name gets the next free folder
    lp._model = FakeModel(pitch_json())
    lp.add("dentists still book on paper too")
    [out2] = lp.pitch()
    assert out2["task_dir"].endswith("clinicbook-2")


def test_pitch_errors_are_counted_then_error(tmp_path):
    def boom(prompt):
        raise RuntimeError("claude -p failed")

    lp = make_loop(tmp_path, model=boom, pitch={"max_errors": 2})
    idea, _ = lp.add("an idea")
    assert lp.pitch()[0]["state"] == "new"
    assert lp.pitch()[0]["state"] == "error"
    assert "claude -p failed" in lp.ledger.get(idea["id"])["reason"]


TYPED = (
    "A SaaS stability rating service\n\nIngest the public release histories of vendor "
    "products and emit a per-vendor stability score from how often their documented "
    "behaviour and defaults changed in the last 24 months."
)
REJECT = {
    "verdict": "reject",
    "product": "Driftwatch",
    "product_type": "web app",
    "who": "platform engineers choosing vendors",
    "buildability": 0.65,
    "buildability_reasons": ["ingest public changelogs", "a score per vendor"],
    "insight": 0.4,
    "differentiation": 0.3,
    "grounding_score": 0.5,
    "reject_reason": "Not a product in its own words: a subscription rating service.",
}


def test_a_typed_task_is_never_refused_by_the_pitch(tmp_path):
    """Every task is typed by the person who runs repo-factory: the pitch writes its brief,
    its doubt is advice, and no floor applies, even when its reply is a rejection."""
    model = FakeModel(json.dumps(REJECT))
    lp = make_loop(
        tmp_path,
        model=model,
        pitch={
            "min_buildability": 0.9,
            "min_insight": 0.5,
            "min_differentiation": 0.4,
            "min_grounding": 0.6,
        },
    )
    idea, _ = lp.add(TYPED)
    [out] = lp.pitch()
    assert pitch.SENT in model.prompts[0]
    assert out["state"] == "queued", out.get("reason")
    p = out["pitch"]
    assert p["verdict"] == "accept" and p["product"] == "Driftwatch"
    assert (
        p["advice"] == "The pitch would have passed: not a product in its own words: a "
        "subscription rating service"
    )
    # no brief in a rejection: the task as written is the brief, as Build anyway does
    spec = (Path(out["task_dir"]) / "spec.md").read_text()
    assert spec.startswith("# Driftwatch") and "stability score" in spec
    assert idea["id"] == out["id"]


def test_the_brief_asks_for_python_unless_the_task_names_a_language():
    from pathlib import Path

    import repo_factory.loop as loop_pkg

    text = (Path(loop_pkg.__file__).parent / "prompts" / "pitch.md").read_text()
    assert 'end the brief with "Write it in Python."' in text
    assert "When the idea names a programming language, keep it in the brief" in text
