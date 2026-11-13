"""Build v5's acceptance: the authority order, strict improvement and regressions
(``repo_factory/v5/accept.py``, pure functions; docs/design/build-v5.md §3.5)."""

from repo_factory.v5 import accept as A

P, F = "passed", "failed"


# -- build v4's strict-improvement rule, copied with its test (core/oneshot.py is frozen) ----------


def M(rules, held=None, tests=None, valid=True):
    return {
        "valid": valid,
        "rules": rules,
        "held": held or {},
        "tests": tests or {},
        "canonical": ["tests/test_existing.py"],
    }


def test_strict_improvement_rule():
    before = M(
        {"a": P, "b": F},
        {"_factory_held/h1": P, "_factory_held/h2": F},
        {"tests/test_existing.py::t": P, "tests/test_own.py::x": P},
    )
    ok, why = A.objective(
        before,
        M(
            {"a": P, "b": P},
            {"_factory_held/h1": P, "_factory_held/h2": F},
            {"tests/test_existing.py::t": P},
        ),
    )
    assert ok, why  # an own test removed is no regression
    assert not A.objective(
        before, M({"a": P, "b": P}, {"_factory_held/h2": F}, {"tests/test_existing.py::t": P})
    )[0]  # held test gone
    assert not A.objective(before, M({"a": P, "b": P}, valid=False))[0]
    ok, why = A.objective(
        before,
        M(
            {"a": P, "b": F},
            {"_factory_held/h1": P, "_factory_held/h2": F},
            {"tests/test_existing.py::t": P},
        ),
    )
    assert not ok and "no improvement" in why
    ok, why = A.objective(
        before,
        M(
            {"a": F, "b": P},
            {"_factory_held/h1": P, "_factory_held/h2": P},
            {"tests/test_existing.py::t": P},
        ),
    )
    assert not ok and "broke 1 rule(s)" in why
    ok, why = A.objective(
        before,
        M(
            {"a": P, "b": P},
            {"_factory_held/h1": P, "_factory_held/h2": F},
            {"tests/test_existing.py::t": F},
        ),
    )
    assert not ok and "test(s)" in why
    assert A.n_passed({"a": P, "b": F, "c": P}) == 2


# -- the authority order ----------------------------------------------------------------------


def test_authority_ranks():
    assert A.rank({"kind": "user_test"}) == 1
    assert A.rank({"kind": "owner"}) == 1
    assert A.rank({"kind": "interpretation", "chosen_by": "owner"}) == 1
    assert A.rank({"kind": "scenario", "basis": "spec_explicit", "required": 3, "votes": 3}) == 2
    assert A.rank({"kind": "scenario", "basis": "spec_explicit", "required": 2, "votes": 3}) == 3
    assert A.rank({"kind": "scenario", "basis": "spec_implied", "required": 3, "votes": 3}) == 3
    assert A.rank({"kind": "builder_test"}) == 4
    assert A.rank({"kind": "interpretation"}) == 5
    # Anything traced to the factory's own assumptions is rank 5 (run 10's q25 and the test).
    assert (
        A.rank(
            {
                "kind": "scenario",
                "basis": "spec_explicit",
                "required": 3,
                "votes": 3,
                "source_ref": "answer:assumptions",
            }
        )
        == 5
    )
    assert A.rank({"kind": "builder_test", "source_ref": "answer:assumptions"}) == 5
    assert A.rank({"kind": "scenario", "required": 1, "votes": 3}) == 5
    assert set(A.AUTHORITY) == {1, 2, 3, 4, 5}


def test_authority_shortcuts_decide_only_incompatible_pairs():
    # compatible is ruled out first, whatever the ranks
    assert A.resolve("compatible", 2, 1) == "fixup"
    assert A.resolve("compatible", 5, 5) == "fixup"
    # different ranks: the higher authority wins, and the panel's x_wins / y_wins is ignored
    assert A.resolve("y_wins", 2, 4) == "keep_x"  # a spec claim beats the builder's test
    assert A.resolve("x_wins", 3, 1) == "revert_x"  # the user's test beats a claim
    assert A.resolve("undecidable", 3, 4) == "keep_x"
    assert A.resolve("undecidable", 4, 2) == "revert_x"
    # equal ranks: the panel decides; undecidable becomes an interpretation item
    assert A.resolve("x_wins", 5, 5) == "keep_x"
    assert A.resolve("y_wins", 5, 5) == "revert_x"
    assert A.resolve("undecidable", 5, 5) == "interpretation"


def V(scen=None, held=None, tests=None, user=None, valid=True):
    return {
        "valid": valid,
        "scenarios": scen or {},
        "held": held or {},
        "tests": tests or {},
        "user": user or {},
    }


def test_decide_counts_fixes_and_lists_every_regression():
    before = V(
        {"q1": F, "q2": F, "q3": P}, {"h1": P}, {"t.py::a": P, "t.py::b": P}, {"tests/u.py::x": P}
    )
    assert A.decide(
        before,
        V(
            {"q1": P, "q2": F, "q3": P},
            {"h1": P},
            {"t.py::a": P, "t.py::b": P},
            {"tests/u.py::x": P},
        ),
        ["q1", "q2"],
    ) == {
        "valid": True,
        "fixed": ["q1"],
        "regressions": [],
        "action": "accept",
        "why": "1 fixed, nothing broken",
    }
    d = A.decide(
        before,
        V(
            {"q1": P, "q2": P, "q3": F},
            {"h1": F},
            {"t.py::a": F},
            {"tests/u.py::x": F},
            valid=False,
        ),
        ["q1", "q2"],
    )
    assert d["action"] == "conflicts" and d["fixed"] == ["q1", "q2"]
    assert {(r["group"], r["id"]) for r in d["regressions"]} == {
        ("scenarios", "q3"),
        ("held", "h1"),
        ("tests", "t.py::a"),
        ("user", "tests/u.py::x"),
    }
    # t.py::b disappeared: an own test the repair removed is not a regression
    assert A.decide(before, before, ["q1"])["action"] == "no_fix"


def test_blame_finds_the_commit_that_broke_it():
    reg = {"id": "t.py::a", "group": "tests"}
    prefixes = [V(tests={"t.py::a": P}), V(tests={"t.py::a": F}), V(tests={"t.py::a": F})]
    assert A.blame(prefixes, reg) == 1
    assert A.blame([V(tests={"t.py::a": P})] * 3, reg) == 2  # only at the head: the last
