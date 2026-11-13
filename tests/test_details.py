"""Details: the record behind a row or a dialog is one read, ``GET api/details?sel=``, in the
design's groups; a stored value of an unexpected shape (a null, a list, a dict, a missing key)
never breaks a record."""

from __future__ import annotations

from repo_factory.ui import details as D

BUILD = {
    "key": "sidedoor",
    "title": "sidedoor",
    "status": "held",
    "desc": "Delivered; the release gate would hold it",
    "reason": "safe_defaults: red: The README's quick-start commands fail.",
    "updated": 1792100000,
    "idea": "i01",
    "fail": None,
    "gate": {"verdict": "held", "checks": [{"name": "safe_defaults", "ok": False}]},
    "tokens": {"in": 1000, "out": 20},
    "cost_usd": None,
    "repo": None,
}
IDEA = {
    "id": "i01",
    "title": "Clinic schedules",
    "status": "open",
    "source": "ui",
    "created": 1792100000,
}


class FakeApp:
    """repo-factory's App as details.record reads it: the builds and a loop task."""

    def builds(self):
        return [BUILD, {"key": "other", "title": None, "status": None}]

    def loop_idea(self, iid):
        return IDEA if iid == "i01" else None


def test_a_build_says_why_it_is_held_and_where_its_page_is():
    rec = D.record(FakeApp(), {"sel": "bsidedoor"})
    rows = {k: v for g in rec["groups"] for k, v in g["rows"]}
    assert "The README's quick-start commands fail." in rows["reason"]
    assert "trace" not in rows and rec["page"] == "#/builds/sidedoor"
    assert rows["updated"].endswith("UTC")


def test_a_shape_it_did_not_expect_says_less_never_fails():
    class Odd(FakeApp):
        def builds(self):
            return [
                None,
                "x",
                {"key": "sidedoor", "fail": "not a dict", "gate": [None, {}], "title": None},
            ]

    rec = D.record(Odd(), {"sel": "bsidedoor"})
    assert rec["title"] == "sidedoor"
    assert D._value(None) is None and D._value([]) is None and D._value({"a": None}) is None
    assert D._value([None, {"title": "x"}, 3]) == "x; 3"
