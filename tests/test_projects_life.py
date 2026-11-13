"""When a project is born: at publish on the fake gh, with the charter drafted at ready as v1;
or when it goes public; and none while projects are off."""

import pytest

from repo_factory.projects.record import Projects

from .loop_common import FakeModel
from .test_charter import RECEIPTS
from .test_loop_gate_publish import built

pytestmark = pytest.mark.usefixtures("projects_on")


@pytest.fixture
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


def test_a_fake_publish_makes_the_project_with_the_charter_drafted_at_ready(tmp_path, fake_gh):
    lp, iid, _ = built(tmp_path, publish={"enabled": True})
    model = FakeModel(RECEIPTS)
    lp._charter = model
    assert lp.gate(iid)["state"] == "ready"
    out = lp.publish(iid)
    assert out["state"] == "published" and len(model.prompts) == 1
    assert (
        "Clinicbook" in model.prompts[0] and '<<<document name="README.md">>>' in model.prompts[0]
    )  # the staged tree's README
    p = Projects(lp.ledger).by_idea(iid)
    assert p["kind"] == "built" and p["repos"][0]["repo"] == "owner/clinicbook"
    assert p["repos"][0]["visibility"] == "private" and p["charter_v"] == 1
    assert p["charter"]["name"] == "Receipts"  # the fake's reply
    notes = [e["note"] for e in Projects(lp.ledger).events(p["id"])]
    assert notes == [
        "charter v1 by repo-factory: drafted when the build was ready",
        "created at publish",
    ]
    lp.approve(iid)
    p = Projects(lp.ledger).by_idea(iid)
    assert p["repos"][0]["visibility"] == "public" and len(Projects(lp.ledger).all()) == 1


def test_create_on_public_waits_for_the_owners_publish(tmp_path, fake_gh):
    lp, iid, _ = built(tmp_path, publish={"enabled": True}, projects={"create": {"on": "public"}})
    lp.gate(iid)
    lp.publish(iid)
    assert Projects(lp.ledger).all() == []
    lp.approve(iid)
    p = Projects(lp.ledger).by_idea(iid)
    assert p and p["charter"] is None  # the tick drafts it later
    assert [e["note"] for e in Projects(lp.ledger).events(p["id"])] == [
        "created when it went public"
    ]


def test_projects_off_makes_none(tmp_path, fake_gh):
    lp, iid, _ = built(tmp_path, publish={"enabled": True}, projects={"enabled": False})
    lp.gate(iid)
    lp.publish(iid)
    assert Projects(lp.ledger).all() == []
