"""loop.yaml checked against DEFAULTS. An unknown key only warns:
a rolled-back repo-factory reads a newer file (test_loop_config_unknown.py), and a typo must never
stop repo-factory. A wrong value under the build's caps or models keeps builds from starting, with
the reason said, instead of running without them."""

import logging

import pytest

from repo_factory.settings import schema
from repo_factory.settings.store import DEFAULTS, Config
from repo_factory.ui.api import HttpError

from .loop_common import make_loop


def test_the_defaults_and_an_empty_file_have_no_problems(tmp_path):
    assert schema.check(DEFAULTS) == [] and schema.check({}) == [] and schema.check(None) == []
    assert Config(tmp_path).problems == []


def test_unknown_keys_are_listed_never_refused(tmp_path, caplog):
    (tmp_path / "loop.yaml").write_text(
        "concurrency: 2\nconcurency: 3\nbuild:\n  retries: 2\n  retires: 1\n"
        "publish:\n  anyway:\n    may_overide: [tests]\n"
    )
    with caplog.at_level(logging.WARNING, logger="repo_factory.settings"):
        cfg = Config(tmp_path)
        Config(tmp_path)  # said once per file version
    assert cfg["concurrency"] == 2 and cfg["build"]["retries"] == 2
    assert {(p["kind"], p["key"]) for p in cfg.problems} == {
        ("unknown", "concurency"),
        ("unknown", "build.retires"),
        ("unknown", "publish.anyway.may_overide"),
    }
    assert schema.refuses_builds(cfg.problems) == []
    said = [r.getMessage() for r in caplog.records if r.name == "repo_factory.settings"]
    assert len(said) == 1 and "3 unknown keys (ignored)" in said[0] and "concurency" in said[0]


def test_free_form_maps_take_any_key(tmp_path):
    (tmp_path / "loop.yaml").write_text(
        "build:\n  models:\n    verify: claude-opus-5-5\n  stage_caps:\n    build: 3.0\n"
    )
    assert Config(tmp_path).problems == []


@pytest.mark.parametrize(
    "text, key",
    [
        ("build:\n  stage_caps: lots\n", "build.stage_caps"),
        ("build:\n  models: [opus]\n", "build.models"),
        ("daily_usd: [5]\n", "daily_usd"),
    ],
)
def test_a_wrong_value_under_the_caps_or_models_refuses_builds(tmp_path, text, key):
    (tmp_path / "loop.yaml").write_text(text)
    bad = schema.refuses_builds(Config(tmp_path).problems)
    assert [p["key"] for p in bad] == [key]
    assert key in schema.describe(bad) and "wrong value" in schema.describe(bad)


def test_a_wrong_value_elsewhere_is_said_but_refuses_nothing(tmp_path):
    (tmp_path / "loop.yaml").write_text("tick_s: soon\nbuild:\n  colour: blue\n")
    cfg = Config(tmp_path)
    assert {p["key"] for p in cfg.problems if p["kind"] == "type"} == {"tick_s"}
    assert {p["key"] for p in cfg.problems if p["kind"] == "unknown"} == {"build.colour"}
    assert schema.refuses_builds(cfg.problems) == []


def test_the_loop_starts_no_build_while_its_caps_are_unreadable(tmp_path):
    lp = make_loop(tmp_path)
    (lp.cfg.home / "loop.yaml").write_text("build:\n  stage_caps: lots\n")
    lp = make_loop(tmp_path)
    idea, _ = lp.add("small clinics still schedule patients on paper")
    lp.pitch([idea["id"]])
    assert lp.start_builds() == []
    assert lp.ledger.get(idea["id"])["state"] in ("queued", "pitched", "sent")  # untouched


def test_the_page_starts_no_build_while_the_caps_are_unreadable(tmp_path, monkeypatch):
    from repo_factory.ui import api

    app = api.App.__new__(api.App)
    app.loop_home = tmp_path
    (tmp_path / "loop.yaml").write_text("build:\n  stage_caps: lots\n")
    with pytest.raises(HttpError) as e:
        app.build_env()
    assert e.value.code == 400 and "build.stage_caps" in str(e.value)
    (tmp_path / "loop.yaml").write_text("build: [unreadable\n")
    with pytest.raises(HttpError) as e:
        app.build_env()
    assert "can't be read" in str(e.value)
    (tmp_path / "loop.yaml").write_text("build:\n  stage_caps: {build: 2.0}\n")
    env = app.build_env()
    assert env is not None and "2.0" in str(env)
