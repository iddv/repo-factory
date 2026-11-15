"""A cap of 0 (or less) is a wrong value, never "no cap": no build starts and the loop names the
line to fix. null stays "no cap", a number stays that cap. Each reader of the four keys
(``daily_usd``, ``build.max_usd``, ``revise.max_usd``, ``maintain.max_usd``) is shown with null,
a number and the refused 0."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repo_factory.loop import builds
from repo_factory.loop.daemon import Loop
from repo_factory.settings import schema

KEYS = {
    "daily_usd": {"daily_usd": 0},
    "build.max_usd": {"build": {"max_usd": 0}},
    "revise.max_usd": {"revise": {"max_usd": 0}},
    "maintain.max_usd": {"maintain": {"max_usd": -1}},
}


@pytest.mark.parametrize("key", sorted(KEYS))
def test_zero_is_a_wrong_value_and_holds_every_build(key):
    probs = schema.check(KEYS[key])
    assert probs == [{"kind": "type", "key": key, "detail": schema.NOT_A_CAP}]
    bad = schema.refuses_builds(probs)
    assert bad and key in schema.describe(bad)


@pytest.mark.parametrize("value", [None, 12.5, 25])
def test_null_and_a_number_are_fine(value):
    for key, data in KEYS.items():
        top, *rest = key.split(".")
        d = {top: value} if not rest else {top: {rest[0]: value}}
        assert schema.check(d) == [], (key, value)


def _loop(daily, today=0.0):
    lp = Loop.__new__(Loop)
    lp.cfg = {"daily_usd": daily}
    lp.spend = lambda: {"today": {"total": today}}
    return lp


def test_the_daily_cap_reader():
    assert _loop(None, 999).over_cap() is False  # no cap
    assert _loop(50, 10).over_cap() is False and _loop(50, 50).over_cap() is True
    assert _loop(0, 0).over_cap() is True  # 0 is reached at once, never "no cap"


def _cfg(build):
    data = {"build": {"policy": None, "owner": None, "harness": None, **build}}
    return SimpleNamespace(get=lambda k, d=None: data.get(k, d), __getitem__=data.__getitem__)


def test_the_build_cap_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(builds, "policy_of", lambda b: tmp_path)

    class Cfg(dict):
        pass

    def args(cap):
        c = Cfg(build={"max_usd": cap})
        return builds.command(c, tmp_path, None, True, None)

    assert "--max-cost" not in args(None)
    a = args(25)
    assert a[a.index("--max-cost") + 1] == "25"
    with pytest.raises(ValueError, match="0 or less is not a cap"):
        args(0)
