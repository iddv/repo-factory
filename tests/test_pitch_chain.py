"""PE2 §6's chain changes (CH): the pitch's scores recorded and gated (``pitch.min_insight``,
``min_differentiation``, ``min_grounding``), a rejection's reason in the owner's words, the
Shape line and the Grounding block in ``spec.md``, the idea block's status line, the card v1
fields, the scope's classifier ignoring what the pitch carries, and the scope's word budget.
No model: the pitch replies are PE2's recorded pitch v2 replies (tests/fixtures/pe2/), replayed
by a fake."""

import json
from pathlib import Path

import pytest

from repo_factory.loop import pitch
from repo_factory.rules.llm import ScriptedLLM
from repo_factory.v5 import scope

from .loop_common import FakeModel, make_loop, pitch_json

PE2 = Path(__file__).parent / "fixtures" / "pe2"
POLICY = Path(__file__).resolve().parents[1] / "policies" / "v5"
GATES = {"min_insight": 0.5, "min_differentiation": 0.4, "min_grounding": 0.5}  # PE2 §5


def reply(name: str, **kw) -> str:
    d = json.loads((PE2 / f"pitch-v2.{name}.json").read_text())
    d.update(kw)
    return "```json\n" + json.dumps(d) + "\n```"


def idea(text="an idea"):
    return {"id": "i1", "source": "cli", "text": text}


# -- the scores and the gate ---------------------------------------------------------------------
def test_the_scores_are_recorded_when_the_prompt_returns_them_and_checked():
    p = pitch.normalise(json.loads(reply("x-context-ledger")[8:-4]))
    assert (p["insight"], p["differentiation"], p["grounding_score"]) == (0.5, 0.45, 0.6)
    assert pitch.check(p) == [] and len(p["grounding"]) == 7
    live = pitch.normalise(json.loads(pitch_json()))  # the live prompt: no scores
    assert not {"insight", "differentiation", "grounding_score", "grounding"} & set(live)
    bad = pitch.normalise(json.loads(pitch_json(insight=1.4, differentiation="high")))
    probs = pitch.check(bad)
    assert "insight must be a number from 0 to 1" in probs
    assert "differentiation must be a number from 0 to 1" in probs


def test_unset_gates_gate_nothing_and_a_reply_without_the_score_is_not_gated():
    assert pitch.gates_of({"min_buildability": 0.6}) == {}
    assert (
        pitch.gates_of({"min_insight": None, "min_differentiation": "x", "min_grounding": True})
        == {}
    )
    assert pitch.gates_of(GATES) == {"insight": 0.5, "differentiation": 0.4, "grounding_score": 0.5}
    # a115 (owner ▼): pitch v2 accepts it at insight 0.45, differentiation 0.35
    assert pitch.run(idea(), FakeModel(reply("a115")))["decision"] == "accepted"
    # the live pitch's reply has no scores: the gates leave it alone
    res = pitch.run(idea(), FakeModel(pitch_json()), gates=pitch.gates_of(GATES))
    assert res["decision"] == "accepted" and "gated" not in res["pitch"]


def test_the_gate_rejects_on_the_new_scores_with_the_reason_in_your_words():
    res = pitch.run(idea(), FakeModel(reply("a115")), gates=pitch.gates_of(GATES))
    p = res["pitch"]
    assert res["decision"] == "rejected"
    assert p["gated"] == {"score": "insight", "value": 0.45, "min": 0.5}
    assert p["reject_reason"].startswith("insight 0.45 is under the loop's minimum 0.50: ")
    assert p["reject_words"].startswith("nothing new in it")
    # differentiation: named after the first existing tool the pitch found
    res = pitch.run(
        idea(),
        FakeModel(
            reply(
                "a115",
                insight=0.6,
                existing=[
                    {"name": "Jaeger", "does": "local trace viewer", "lacks": "nothing much"}
                ],
            )
        ),
        gates=pitch.gates_of(GATES),
    )
    assert res["pitch"]["gated"]["score"] == "differentiation"
    assert res["pitch"]["reject_words"] == "too close to Jaeger, which already does this"
    # grounding
    res = pitch.run(
        idea(),
        FakeModel(reply("a115", insight=0.6, differentiation=0.6)),
        gates=pitch.gates_of(GATES),
    )
    assert res["pitch"]["gated"]["score"] == "grounding_score"
    assert res["pitch"]["reject_words"] == "its specifics don't come from its sources"
    # buildability still comes first, in its own words
    res = pitch.run(
        idea(), FakeModel(reply("a115", buildability=0.3)), 0.6, gates=pitch.gates_of(GATES)
    )
    assert res["pitch"]["gated"]["score"] == "buildability"
    assert res["pitch"]["reject_words"] == "too big or too vague to build in one go"


def test_the_pitchs_own_rejection_gets_words_too():
    res = pitch.run(idea(), FakeModel(reply("a113")))  # #113, owner ▼
    assert res["decision"] == "rejected"
    assert res["pitch"]["reject_words"] == "too close to Jaeger all-in-one, which already does this"
    res = pitch.run(
        idea(), FakeModel(pitch_json(verdict="reject", reject_reason="It is a research bet. More."))
    )
    assert res["pitch"]["reject_words"] == "it is a research bet"
    assert (
        pitch.reject_words({"reject_reason": "LiteLLM Proxy already covers it"})
        == "LiteLLM Proxy already covers it"
    )


# -- spec.md -------------------------------------------------------------------------------------
def test_spec_md_carries_the_shape_and_the_grounding():
    p = pitch.normalise(json.loads(reply("x-context-ledger")[8:-4]))
    spec = pitch.spec_text(p)
    head, rest = spec.split("\n\n", 1)
    assert head == "# Context Ledger"
    assert rest.startswith("Shape: single user, cli, no money\n\n" + p["brief"].strip())
    assert "\nGrounding:\n" + "".join(f"- {g}\n" for g in p["grounding"]) in spec
    # the live pitch (no grounding): the Shape line only
    live = pitch.spec_text(pitch.normalise(json.loads(pitch_json())))
    assert "Shape: business, web app, no money" in live and "Grounding" not in live
    # no product type: no Shape line; at most eight grounding lines
    odd = pitch.spec_text({"product": "X", "brief": "b", "grounding": [str(n) for n in range(12)]})
    assert "Shape" not in odd and odd.count("\n- ") == 8


@pytest.mark.parametrize(
    "who,brief,want",
    [
        ("receptionists at small clinics", "book appointments", "business, web app, no money"),
        ("A shop owner", "send invoices and take payments", "business, web app, money"),
        ("Maintainers who keep issues next to their code", "file issues", "small team, web app,"),
        ("An engineer on a team shipping a RAG feature", "strip evidence", "single user, web app,"),
        ("A small-site operator whose chat clients query a feed", "serve it", "single user,"),
        (
            "A solo developer on pay-per-token APIs",
            "replay prompts, print the cost gap",
            "single user, web app, no money",
        ),
    ],
)
def test_the_shape_is_computed_from_who_and_the_product_type(who, brief, want):
    assert pitch.shape({"product_type": "web app", "who": who, "brief": brief}).startswith(
        "Shape: " + want
    )


# -- the scope -----------------------------------------------------------------------------------
def test_the_classifier_ignores_the_shape_and_the_grounding():
    p = pitch.normalise(json.loads(reply("x-context-ledger")[8:-4]))
    spec = pitch.spec_text(p)
    got = scope.carried(spec)
    assert got["shape"] == "single user, cli, no money" and got["grounding"] == p["grounding"]
    assert "Grounding" not in got["core"] and "Shape" not in got["core"]
    assert scope.classify(spec) == scope.classify(
        pitch.spec_text({**p, "grounding": [], "product_type": None})
    )
    assert scope.classify(spec)["verdict"] != "spec"


class Priced(ScriptedLLM):
    """ScriptedLLM with a cost per mode (None: the scope call, "trim": the shortening pass)."""

    def __init__(self, handlers, usd):
        super().__init__(handlers)
        self.price = usd

    def complete(self, prompt, task, mode):
        text, _ = super().complete(prompt, task, mode)
        return text, self.price.get(mode, 0.0)


def scope_md(n_words: int, shape: str | None = None) -> str:
    """A parsable SCOPE.md of exactly ``n_words`` words."""

    def md(body):
        return (
            f"# Scope: Thing\nOne paragraph.{(' Shape: ' + shape + '.') if shape else ''}\n\n"
            "Interfaces: command line\n\n## Users\nOne person.\n\n## Core flows\n"
            "### F1: Install and first run\n- Steps: install\n\n## Features\n"
            "### Must have (v1)\n- M1: it works (F1)\n### Later (not in v1)\n- more: later\n\n"
            f"## Notes\n{body}\n\n## Definition of done\n"
            "- [ ] F1: Install and first run — it starts\n"
        )

    return md(" ".join(["word"] * (n_words - scope.words(md("")))))


def test_an_over_long_scope_gets_one_shortening_pass_and_both_lengths_are_kept():
    idea_text = "# Thing\n\nShape: single user, cli, no money\n\nA short idea for one person."
    seen = []

    def handler(prompt, mode):
        seen.append(mode)
        return scope_md(1500) if mode == "trim" else scope_md(2000)

    llm = Priced({"scope": handler}, {None: 0.3, "trim": 0.2})
    res = scope.run(llm, POLICY, idea_text, "auto")
    assert seen == [None, "trim"]
    assert "is 2000 words" in llm.prompts[1] and "1800 words" in llm.prompts[1]
    sz = res["size"]
    assert (sz["limit"], sz["words_first"], sz["words"], sz["trimmed"]) == (1800, 2000, 1500, True)
    assert sz["trim_usd"] == pytest.approx(0.2) and sz["why"].startswith("the pitch's shape")
    assert res["decision"] == "scoped" and scope.words(res["md"]) == 1500
    assert scope.record(res)["size"]["words_first"] == 2000  # scope.json keeps it


def test_the_shortening_pass_is_capped_skipped_or_discarded():
    idea_text = "# Thing\n\nShape: single user, cli, no money\n\nA short idea for one person."
    # the first call cost more than the pass's cap: no pass
    llm = Priced({"scope": scope_md(2000)}, {None: 0.9})
    res = scope.run(llm, POLICY, idea_text, "auto", size={"trim_max_usd": 0.5})
    assert llm.calls == 1 and not res["size"]["trimmed"] and "over its cap" in res["size"]["note"]
    # 0: the pass is off
    llm = Priced({"scope": scope_md(2000)}, {None: 0.1})
    res = scope.run(llm, POLICY, idea_text, "auto", size={"trim_max_usd": 0})
    assert llm.calls == 1 and "off" in res["size"]["note"]
    # a reply that is not shorter, or not a scope, is not used
    for bad in (scope_md(2100), "sorry"):
        llm = Priced({"scope": lambda p, m, b=bad: b if m == "trim" else scope_md(2000)}, {})
        res = scope.run(llm, POLICY, idea_text, "auto")
        assert llm.calls == 2 and not res["size"]["trimmed"] and res["size"]["words"] == 2000
    # under the limit: one call; a team shape gets 2,500 words
    llm = Priced({"scope": scope_md(2200)}, {})
    res = scope.run(llm, POLICY, "# T\n\nShape: small team, web app, no money\n\nAn idea.", "auto")
    assert llm.calls == 1 and res["size"]["limit"] == 2500 and not res["size"]["trimmed"]
    # no Shape line in the idea: the scope's own, else the larger limit
    llm = Priced(
        {
            "scope": lambda p, m: (
                scope_md(1900, "single user, cli, no money") if m is None else scope_md(1700)
            )
        },
        {},
    )
    res = scope.run(llm, POLICY, "A short idea for one person.", "auto")
    assert res["size"]["limit"] == 1800 and res["size"]["why"].startswith("the scope's shape")
    assert scope.limit_of("An idea.", scope_md(100), scope.SIZE) == (2500, "no shape given")


# -- the replay: PE2's flow seeds through the loop's code path -----------------------------------
SEEDS = {  # reply -> whether the pitch, with PE2 §5's gates, would have passed it
    "x-context-ledger": True,
    "x-hook-selftest": True,
    "x-json-mcp": True,
    "x-cost-replay": False,
    "a115": False,
    "a113": False,
    "x-evidence-ablation": False,
}


def test_pe2_flow_seeds_replayed_through_the_loop(tmp_path):
    """Every task is typed by its owner: none is refused; a reply the gates would have refused
    is queued all the same, and the scores are recorded either way."""
    seen = {}
    for name, passes in SEEDS.items():
        lp = make_loop(tmp_path / name, model=FakeModel(reply(name)), pitch=GATES)
        lp.add(f"the {name} idea")
        [out] = lp.pitch()
        rec = out["pitch"]
        assert out["state"] == "queued", name
        for k in ("insight", "differentiation", "grounding_score"):
            assert isinstance(rec[k], float), (name, k)  # recorded in the ledger
        assert not rec.get("gated"), name  # no floor on a typed task
        if not passes:
            continue
        spec = (Path(out["task_dir"]) / "spec.md").read_text()
        assert "\nShape: single user, cli, no money\n" in spec
        assert all(f"- {g}\n" in spec for g in rec["grounding"]) and "\nGrounding:\n" in spec
        seen[name] = spec
    # the scope reads both: the spec is scoped (not passed through as a spec), and the Shape
    # line and the Grounding block are in the prompt it sends
    spec = seen["x-context-ledger"]
    llm = Priced({"scope": scope_md(900)}, {})
    res = scope.run(llm, POLICY, spec, "auto")
    assert res["called"] and res["decision"] == "scoped"
    assert "Shape: single user, cli, no money" in llm.prompts[0]
    assert scope.carried(spec)["grounding"][0] in llm.prompts[0]
    assert res["size"]["limit"] == 1800
    # without the gates (the default), the same replies pass as before
    lp = make_loop(tmp_path / "nogate", model=FakeModel(reply("x-cost-replay")))
    lp.add("the cost replay idea")
    assert lp.pitch()[0]["state"] == "queued"
