"""Each action's precondition follows the subject's live state, and each runs only through the
``App`` method the page's button calls (registry.check, registry.run); ``repo-factory loop edit``.
The registry is what the page, the command line and an agent's token run."""

import pytest

from repo_factory import registry as R
from repo_factory.cli import main
from repo_factory.errors import LoopError


def S(subject: dict, idea=None, row=None) -> R.Subject:
    return R.Subject(subject.get("kind") or "new", idea, row, None, subject.get("id"))


def check(kind, subject, idea, row):
    return R.check(kind, S(subject, idea, row))


def test_preconditions_follow_the_live_state():
    new = {"kind": "new", "id": None}
    idea = lambda st, **kw: {"id": "i12345678", "state": st, **kw}  # noqa: E731
    sub = {"kind": "idea", "id": "i12345678"}
    assert check("add_idea", new, None, None) is None
    assert check("add_idea", sub, idea("new"), None)
    assert check("send_to_build", new, None, None) is None
    assert check("send_to_build", sub, idea("new"), None) is None
    assert check("send_to_build", sub, idea("new", sent=True), None)
    assert check("send_to_build", sub, idea("queued"), None)
    assert check("build_anyway", sub, idea("rejected", pitch={"product": "P"}), None) is None
    assert check("build_anyway", sub, idea("rejected", pitch={}), None)
    assert check("pitch_again", sub, idea("closed"), None) is None
    assert check("retry", sub, idea("held"), None) is None
    assert check("retry", sub, idea("published"), None)
    ready = {"idea": "i12345678", "idea_state": "ready", "status": "ready"}
    assert check("publish", {"kind": "build", "id": "k"}, None, ready) is None
    row = {"idea": "i12345678", "idea_state": "published", "status": "ready"}
    assert check("publish", {"kind": "build", "id": "k"}, None, row)
    assert check("make_public", {"kind": "build", "id": "k"}, None, row) is None
    assert check(
        "make_public",
        {"kind": "build", "id": "k"},
        None,
        {**row, "idea_state": "public", "status": "public"},
    )
    assert check("close", {"kind": "build", "id": "k"}, None, row) is None
    assert check("close", sub, idea("public"), None)
    assert check("close", {"kind": "build", "id": "k"}, None, {"idea": None, "status": "failed"})
    assert check("edit_brief", sub, idea("rejected"), None) is None
    assert check("edit_brief", sub, idea("building"), {"live": True, "state": "running"})
    assert check("edit_brief", new, None, None)


class Spy:
    """Records the App calls an action makes (the page's own methods)."""

    def __init__(self):
        self.calls = []

    def loop_act(self, what, body):
        self.calls.append((what, body))
        return {"ok": True, "id": body.get("id") or "i00000001", "out": "ok"}

    def start(self, d, policy):
        self.calls.append(("start", str(d), policy))
        return {"ok": True}


def run(spy, kind, fields, subject, idea, row):
    return R.run(R.Ctx(spy, "ui", confirm=True), kind, fields, S(subject, idea, row), record=False)


def test_each_kind_calls_the_pages_method():
    sub = {"kind": "idea", "id": "i12345678"}
    cases = [
        (
            "add_idea",
            {"kind": "new", "id": None},
            None,
            None,
            {"text": "t"},
            [("add", {"text": "t"})],
        ),
        (
            "send_to_build",
            {"kind": "new", "id": None},
            None,
            None,
            {"text": "t"},
            [("add", {"text": "t"}), ("send", {"id": "i00000001"})],
        ),
        (
            "send_to_build",
            sub,
            {"id": "i12345678", "state": "rejected"},
            None,
            {},
            [("send", {"id": "i12345678"})],
        ),
        (
            "build_anyway",
            sub,
            {"id": "i12345678", "state": "rejected", "pitch": {"product": "P"}},
            None,
            {},
            [("send", {"id": "i12345678", "anyway": True})],
        ),
        (
            "pitch_again",
            sub,
            {"id": "i12345678", "state": "closed"},
            None,
            {},
            [("send", {"id": "i12345678"})],
        ),
        (
            "retry",
            sub,
            {"id": "i12345678", "state": "held"},
            None,
            {},
            [("retry", {"id": "i12345678"})],
        ),
        (
            "retry",
            sub,
            {"id": "i12345678", "state": "held"},
            None,
            {"note": "the login expired"},
            [("retry", {"id": "i12345678", "note": "the login expired"})],
        ),
        (
            "close",
            sub,
            {"id": "i12345678", "state": "new"},
            None,
            {"reason": " not  now "},
            [("close", {"id": "i12345678", "reason": "not now"})],
        ),
        (
            "edit_brief",
            sub,
            {"id": "i12345678", "state": "new"},
            None,
            {"text": "b"},
            [("edit", {"id": "i12345678", "text": "b"})],
        ),
    ]
    for kind, subject, idea, row, fields, want in cases:
        spy = Spy()
        run(spy, kind, fields, subject, idea, row)
        assert spy.calls == want, kind
    with pytest.raises(R.ActionError):
        run(Spy(), "close", {"reason": "  "}, sub, {"id": "i12345678", "state": "new"}, None)
    with pytest.raises(R.ActionError):  # the state moved on
        run(Spy(), "retry", {}, sub, {"id": "i12345678", "state": "public"}, None)


def test_revise_on_a_held_build_and_a_ticket_on_a_published_one():
    sub = {"kind": "build", "id": "k"}
    row = {
        "key": "k",
        "idea": "i12345678",
        "idea_state": "held",
        "status": "held",
        "gate": {"verdict": "held", "checks": {"delivered": {"ok": True}}},
    }
    assert check("revise", sub, None, row) is None
    assert check("revise", sub, None, {**row, "idea_state": "ready", "status": "running"}) is None
    pub = {
        **row,
        "idea_state": "published",
        "status": "ready",
        "repo": "o/p",
        "url": "https://github.com/o/p",
    }
    assert "Open issue" in check("revise", sub, None, pub)
    assert check("revise", sub, None, {**row, "idea_state": "building", "status": "running"})
    assert check("revise", sub, None, {**row, "gate": {"verdict": "held", "reasons": ["x"]}})
    spy = Spy()
    run(
        spy,
        "revise",
        {"instructions": "README line 4: say history can be purged", "mode": "docs"},
        sub,
        None,
        row,
    )
    assert spy.calls == [
        (
            "revise",
            {"id": "i12345678", "mode": "docs", "text": "README line 4: say history can be purged"},
        )
    ]


def test_no_action_runs_without_a_press_and_chat_is_no_longer_an_origin():
    """TSI-6: the chat's act-without-asking settings (chat.auto) went with it; nothing in the
    registry reads them, and a request that says origin chat is refused."""
    assert not hasattr(R, "auto_on") and not hasattr(R, "AUTO_DEFAULTS")
    assert all(not hasattr(a, "auto") and not hasattr(a, "chat") for a in R.ACTIONS.values())
    assert "chat" not in R.ORIGINS and not R.valid_origin("chat")
    with pytest.raises(R.ActionError, match="origin must be"):
        R.run(R.Ctx(Spy(), "chat"), "add_idea", {"text": "t"}, S({"kind": "new"}), record=False)


def test_cli_edit_rewrites_a_queued_spec_until_the_build_starts(tmp_path, capsys):
    import yaml

    from .loop_common import make_loop

    lp = make_loop(tmp_path)
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    a, _ = lp.add("small clinics still schedule patients on paper")
    lp.pitch([a["id"]])
    i = lp.ledger.get(a["id"])
    assert i["state"] == "queued"
    p = tmp_path / "brief.txt"
    p.write_text("Clinics: book, remind, no double bookings.")
    main(["loop", "--home", str(lp.cfg.home), "edit", a["id"], "--file", str(p)])
    assert "Brief saved" in capsys.readouterr().out
    from pathlib import Path

    spec = (Path(i["task_dir"]) / "spec.md").read_text()
    assert spec.startswith("# Clinicbook\n") and "no double bookings" in spec
    assert lp.ledger.get(a["id"])["pitch"]["brief"].startswith("Clinics:")
    lp.ledger.update(a["id"], state="building")
    with pytest.raises(LoopError):
        lp.edit(a["id"], "too late")
