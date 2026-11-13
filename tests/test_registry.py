"""The registry: one table of actions for the page, the command line and an agent's token.
Each kind is complete; the confirm class is exactly its kinds; nothing in it runs on a
setting; the fields are checked; and ``when`` (the words on the page) agrees with the
``Loop`` method behind each verb (the authority), state by state, on a scratch ledger."""

import json

import pytest

from repo_factory import registry as R
from repo_factory.errors import LoopError
from repo_factory.loop.ledger import STATES
from repo_factory.ui import api

from .loop_common import make_loop


def test_every_kind_is_complete():
    for k, a in R.ACTIONS.items():
        assert a.kind == k and a.title and a.says and a.click in ("click", "confirm"), k
        assert a.subjects and set(a.subjects) <= {"new", "idea", "build", "loop"}, k
        s = R.schema(k)
        assert s["type"] == "object" and s["additionalProperties"] is False, k
        for f in a.fields:
            assert f.name in s["properties"], (k, f.name)
        json.dumps(s)


def test_the_confirm_class_is_exactly_these_kinds():
    # one build's controls that stop work are confirm class too (Resume build is a click);
    # a scope change changes what the project is (its charter)
    assert (
        {k for k, a in R.ACTIONS.items() if a.click == "confirm" and k != "stop"}
        == set(R.CONFIRM_CLASS)
        == {
            "publish",
            "make_public",
            "publish_anyway",
            "close",
            "build_anyway",
            "stop_round",
            "cancel",
            "restart_revise",
            "accept_claim",
            "charter_change",
            # making, replacing or removing repo-factory's own credential
            "github_app_create",
            "github_app_key",
            "github_app_remove",
            # linking an app already made on GitHub keeps its key
            "github_app_link",
            # forgetting repo-factory's GitHub connection
            "github_disconnect",
            # forgetting the Claude login builds use
            "claude_login_forget",
            # the merge policy, a pull request on .github/repo-factory.yml
            "maintain_merge_policy",
        }
    )
    assert R.ACTIONS["resume_build"].click == "click"
    for k in R.CONFIRM_CLASS:
        s = R.schema(k)
        assert s["properties"]["confirm"]["const"] is True and "confirm" in s["required"]
    assert "confirm" not in R.schema("retry")["properties"]


def test_nothing_runs_on_a_setting():
    """TSI-6: the chat's act-without-asking settings went with the chat; no action has a
    setting that runs it, and none reads loop.yaml's chat.auto."""
    assert not hasattr(R, "auto_on") and not hasattr(R, "AUTO_DEFAULTS")
    assert all("auto" not in R.describe(a) for a in R.ACTIONS.values())
    assert "auto" not in R.Ctx.__dataclass_fields__


@pytest.mark.parametrize(
    "kind, raw, why",
    [
        ("add_idea", {}, "needs the text"),
        ("add_idea", {"text": "x" * 4001}, "over 4000"),
        ("close", {"reason": 5}, "must be text"),
        ("close", {}, "needs reason"),
        ("later", {"days": 0}, "from 1 to 365"),
        ("later", {"days": "soon"}, "whole number"),
        ("reject", {"reasons": ["meh"]}, "must come from"),
        ("publish_anyway", {"reason": "too short"}, "at least 12"),
        ("publish_anyway", {"reason": "x" * 301}, "over 300"),
        ("cut_ticket", {"issue": "t", "text": "b", "labels": ["bug", "feature"]}, "one kind"),
        ("cut_ticket", {"text": "b", "labels": ["bug"]}, "issue's title"),
        ("nope", {}, "unknown action"),
    ],
)
def test_validate_says_what_is_wrong(kind, raw, why):
    with pytest.raises(R.ActionError, match=why) as e:
        R.validate(kind, raw)
    assert e.value.code == 400


def test_validate_cleans_and_a_card_may_leave_the_dialogs_field():
    assert R.validate("close", {"reason": "  not   now "}) == {"reason": "not now"}
    assert R.validate("close", {}, card=True) == {}
    assert R.validate("later", {"days": "30"}) == {"days": 30}
    assert R.validate("retry", {"note": "x " * 400})["note"].endswith("…")


def row(status, idea_state=None, **kw):
    return {
        "key": "k",
        "dir": "/nowhere",
        "status": status,
        "state": kw.pop("state", "new"),
        "idea": "i12345678" if idea_state else None,
        "idea_state": idea_state,
        **kw,
    }


GATE_TESTS = {
    "verdict": "held",
    "round": 2,
    "checks": {
        "delivered": {"ok": True, "detail": "ok"},
        "tests": {"ok": False, "detail": "4 of 58 of the product's own tests fail"},
    },
}
GATE_SEC = {
    "verdict": "held",
    "round": 2,
    "checks": {
        "delivered": {"ok": True, "detail": "ok"},
        "security": {"ok": False, "detail": "open: high: x"},
    },
}
CHECKED = {"state": "done", "since": 1.0, "holds": []}  # the pre-push checks ran: clean


@pytest.mark.parametrize(
    "r, enabled",
    [
        # a held build that delivered: Re-check with current repo-factory too (EX-PLAN §7)
        (
            row("held", "held", gate=GATE_TESTS, precheck=CHECKED),
            {"retry", "close", "publish_anyway", "revise", "recheck"},
        ),
        # the pre-push checks still running: Publish anyway waits (it never runs a model)
        (
            row("held", "held", gate=GATE_TESTS, precheck={"state": "running", "since": 1.0}),
            {"retry", "close", "revise", "recheck"},
        ),
        (row("held", "held", gate=GATE_SEC), {"retry", "close", "revise", "recheck"}),
        (
            row("held", "held", gate={"verdict": "held", "reasons": ["login expired"]}),
            {"retry", "close"},
        ),
        # a ready build waits for the owner's press to publish it privately
        (row("ready", "ready"), {"publish", "close"}),
        (
            row("ready", "published", repo="o/p"),
            {"make_public", "keep", "reject", "close", "cut_ticket"},
        ),
        (row("kept", "published", repo="o/p"), {"make_public", "reject", "close", "cut_ticket"}),
        (row("public", "public", repo="o/p"), {"cut_ticket"}),
        (row("running", "building"), {"close", "stop_round"}),
        # a revise round running can be restarted; a stop under way offers nothing more
        (
            row("running", "building", change={"k": "1", "mode": "code"}),
            {"close", "stop_round", "restart_revise"},
        ),
        (row("running", "building", control={"what": "stop", "at": 1.0}), {"close"}),
        (row("paused", "paused"), {"close", "stop_round", "resume_build"}),
        (row("queued", "queued"), {"close", "cancel"}),
        (
            row(
                "held",
                "held",
                gate=GATE_TESTS,
                precheck=CHECKED,
                stopped={"round": 3, "resumable": True},
            ),
            {"retry", "close", "publish_anyway", "revise", "resume_build", "recheck"},
        ),
        (row("failed"), {"retry"}),
        (row("held", state="delivered"), {"retry", "reject"}),
        (row("running", state="running"), {"stop"}),
        (
            row("held", state="asking", question={"id": "q1", "text": "?"}),
            {"retry", "answer", "stop"},
        ),
    ],
)
def test_available_for_a_build(r, enabled):
    s = R.Subject("build", None, r)
    got = {a["kind"] for a in R.available(s) if a["enabled"]}
    assert got == enabled
    for a in R.available(s):
        assert a["enabled"] == (a["why"] is None) and a["title"] and a["says"]


def test_publish_anyway_says_why_it_is_hidden():
    a = {
        x["kind"]: x
        for x in R.available(R.Subject("build", None, row("held", "held", gate=GATE_SEC)))
    }
    assert not a["publish_anyway"]["enabled"] and "security" in a["publish_anyway"]["why"]
    bind = {
        **GATE_TESTS,
        "checks": {
            **GATE_TESTS["checks"],
            "safe_defaults": {"ok": False, "detail": "red: Listens", "red": ["bind_localhost"]},
        },
    }
    a = {
        x["kind"]: x for x in R.available(R.Subject("build", None, row("held", "held", gate=bind)))
    }
    assert "loopback" in a["publish_anyway"]["why"]
    docs = {
        **GATE_TESTS,
        "checks": {**GATE_TESTS["checks"], "documents": {"ok": False, "detail": "missing"}},
    }
    s = R.Subject("build", None, row("held", "held", gate=docs, precheck=CHECKED))
    assert "may_override" in R.check("publish_anyway", s)
    s.cfg = {"publish": {"anyway": {"may_override": ["tests", "documents"]}}}
    assert R.check("publish_anyway", s) is None


def test_publish_anyway_lists_every_hold_and_waits_for_the_checks():
    """PA: the dialog's list is the gate's holds and the pre-push checks', each may or never;
    while the checks run the press is refused (no model at the press)."""
    # wording never holds (2026-10-16): the pre-push checks add no hold of their own here
    pre = {"state": "done", "since": 1.0, "holds": []}
    s = R.Subject("build", None, row("held", "held", gate=GATE_TESTS, precheck=pre))
    a = {x["kind"]: x for x in R.available(s)}["publish_anyway"]
    assert a["enabled"] and a["plan"]["ok"] and a["plan"]["checking"] is None
    got = [(x["check"], x["may"]) for x in a["plan"]["holds"]]
    assert got == [("tests", True)]
    pre = {"state": "done", "since": 1.0, "holds": [["secrets", "config.py:1 GitHub token"]]}
    s = R.Subject("build", None, row("held", "held", gate=GATE_TESTS, precheck=pre))
    a = {x["kind"]: x for x in R.available(s)}["publish_anyway"]
    assert not a["enabled"] and "secrets are never pushed" in a["why"]
    assert ("secrets", False) in [(x["check"], x["may"]) for x in a["plan"]["holds"]]
    for st in ({"state": "running", "since": 1.0}, {"state": "stale"}, None):
        s = R.Subject("build", None, row("held", "held", gate=GATE_TESTS, precheck=st))
        a = {x["kind"]: x for x in R.available(s)}["publish_anyway"]
        assert not a["enabled"] and "pre-push checks" in a["why"] and a["plan"]["checking"]
        assert [x["check"] for x in a["plan"]["holds"]] == ["tests"]  # the gate's, meanwhile
        with pytest.raises(R.ActionError, match="pre-push checks"):
            R.run(
                R.Ctx(Spy(), "ui", confirm=True),
                "publish_anyway",
                {"reason": "the four failures are flaky"},
                s,
                record=False,
            )
    # the press sends what the owner saw: a different list is refused before the loop runs
    s = R.Subject("build", None, row("held", "held", gate=GATE_TESTS, precheck=CHECKED))
    spy = Spy()
    with pytest.raises(R.ActionError, match="holds changed"):
        R.run(
            R.Ctx(spy, "ui", confirm=True),
            "publish_anyway",
            {"reason": "the four failures are flaky", "holds": ["tests", "jargon"]},
            s,
            record=False,
        )
    assert spy.calls == []
    R.run(
        R.Ctx(spy, "ui", confirm=True),
        "publish_anyway",
        {"reason": "the four failures are flaky", "holds": ["tests"]},
        s,
        record=False,
    )
    assert spy.calls[0][0] == "publish-anyway" and spy.calls[0][1]["holds"] == ["tests"]


class Spy:
    def __init__(self):
        self.calls = []

    def loop_act(self, what, body):
        self.calls.append((what, body, R.current_origin()))
        return {"ok": True, "out": "ok"}


def test_run_refuses_the_confirm_class_without_confirm_and_a_bad_origin():
    s = R.Subject("build", None, row("ready", "ready"))
    with pytest.raises(R.ActionError) as e:
        R.run(R.Ctx(Spy(), "agent:RC"), "publish", {}, s, record=False)
    assert e.value.code == 409 and e.value.confirm and "needs confirm: true" in str(e.value)
    for gone in ("chat", "mcp"):  # no door any more
        with pytest.raises(R.ActionError, match="origin"):
            R.run(R.Ctx(Spy(), gone), "publish", {}, s, record=False)
    spy = Spy()
    R.run(R.Ctx(spy, "agent:RC", confirm=True), "publish", {}, s, record=False)
    assert spy.calls == [("publish", {"id": "i12345678"}, "agent:RC")]
    s = R.Subject("build", None, row("ready", "published"))
    spy = Spy()
    R.run(R.Ctx(spy, "ui", confirm=True), "make_public", {}, s, record=False)
    assert spy.calls == [("approve", {"id": "i12345678"}, "ui")]
    with pytest.raises(R.ActionError, match="origin"):
        R.run(R.Ctx(Spy(), "web"), "keep", {}, s, record=False)


# -- when agrees with the Loop ---------------------------------------------------------------
# kind: (Loop call, exact). exact: ``when`` is None exactly when the verb succeeds. Not exact:
# the page offers the action in fewer states than the verb accepts (Pitch again on an idea
# that is not new; Publish on a repo that is already private; Reject on a build ready to go
# public, Close elsewhere): when None implies the verb succeeds.
VERBS = {
    "send_to_build": (lambda lp, i: lp.send(i), True),
    "build_anyway": (lambda lp, i: lp.build_anyway(i), True),
    "retry": (lambda lp, i: lp.retry(i), True),
    "close": (lambda lp, i: lp.close(i, "no thanks"), True),
    "keep": (lambda lp, i: lp.keep(i), True),
    "later": (lambda lp, i: lp.later(i, 3), True),
    "edit_brief": (lambda lp, i: lp.edit(i, "a new brief"), True),
    "pitch_again": (lambda lp, i: lp.send(i), False),
    # Publish (ready -> private) re-runs the gate on a delivered tree, which a scratch ledger
    # row has not got: tests/test_loop_gate_publish.py presses it on a real build
    "make_public": (lambda lp, i: lp.approve(i), False),
    "reject": (lambda lp, i: lp.reject(i, ["bugs"]), False),
    # RC: one build's controls (no process runs here: a stop finishes at once)
    "stop_round": (lambda lp, i: lp.stop_round(i), True),
    "resume_build": (lambda lp, i: lp.resume_build(i), True),
    "cancel": (lambda lp, i: lp.cancel(i), True),
}


class OkGh:
    """``gh`` that says yes to everything (approve flips visibility through it)."""

    def __call__(self, *a, **kw):
        return ""


def scratch(tmp_path, state, kept=False):
    lp = make_loop(tmp_path)
    a, _ = lp.add("small clinics still schedule patients on paper")
    i = lp.pitch([a["id"]])[0]
    pub = (
        {
            "repo": "owner/clinicbook",
            "url": "https://github.com/owner/clinicbook",
            **({"kept": 1.0} if kept else {}),
        }
        if state in ("published", "public")
        else None
    )
    lp.ledger.update(i["id"], state=state, publish=pub)
    return lp, i["id"]


@pytest.mark.parametrize("state", [*STATES, "kept"])
def test_when_agrees_with_the_loop(tmp_path, monkeypatch, state):
    monkeypatch.setenv("FAKE_GH_DIR", str(tmp_path / "gh"))
    for kind, (verb, exact) in VERBS.items():
        d = tmp_path / kind
        lp, iid = scratch(d, "published" if state == "kept" else state, kept=state == "kept")
        app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
        s = R.subject_of(app, "idea", iid)
        why = R.check(kind, s)
        if not exact and why is not None:
            continue
        lp._gh = OkGh()
        try:
            verb(lp, iid)
            ok = True
        except LoopError:
            ok = False
        if exact:
            assert (why is None) == ok, (state, kind, why, ok)
        elif why is None:
            assert ok, (state, kind)
